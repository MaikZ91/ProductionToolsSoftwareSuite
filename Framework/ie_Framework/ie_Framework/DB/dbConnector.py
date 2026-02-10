import logging
import os.path
import socket
import time
from datetime import datetime
from io import StringIO
import socks
from ie_Framework.Utility import miltenyiBarcode
import pandas
import enum

SQLDATETIMEFORMAT = "%Y-%m-%d_%H:%M:%S"
SQLDATEFORMAT = "%y-%m-%d"
SQLTIMEFORMAT = "%H:%M:%S"
CHUNK_SIZE = 4096  # 4 KB pro Chunk – üblich und sicher
DB_CONNECTOR_VERSION = "1.2.7"


class DetailLevel(enum.Enum):
    LowLoss = 0
    NoLoss = 1


class connection():
    """Class for establishing and managing a connection to the ie-Applicationserver MDEBGLPRDSPCP01. It provides methods
     for writing an reading tests from a database. See
     https://confluence.miltenyibiotec.de/spaces/IE/pages/299371734/Framework#Framework-Datenbank
     for further documentation"""
    _instance = None

    def __init__(self):
        """Creates a new object of type connection. The connection has to be established via self.connect()"""
        self.host = "MDEBGLPRDSPCP01"
        self.port = 50001
        self.valid = True
        self.connected = False
        self.database = ""
        self.dbPort = 0
        self.comm_socket = None
        self.testEquipt = None
        self.debugging = False
        self.__throwErrors = True

    def __enter__(self):
        """This method is called when a connection object is created with an with-Statement"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """This method is called when leaving a with statement"""
        self.disconnect()

    def connect(self) -> int:
        """Method for establishing a connection to the target server."""
        try:
            client_socket = socks.socksocket()
            client_socket.setblocking(True)
            client_socket.settimeout(2)
            client_socket.connect((self.host, self.port))  # connect to the server

            # message = input(" -> ")  # take input
            connectInfo = client_socket.recv(4096).decode("utf-8")
            infos = connectInfo.split(";")
            if len(infos) < 2:
                if len(infos) == 0:
                    return -3
                self.valid = False
                self.connected = False
                logging.warning("No valid connect answer from %s." % self.host)
                return -2
            else:
                # $send start=24-01-01_19:00:00 end=24-01-01_20:00:00 result=0 test=Dummy;
                self.comm_socket = client_socket
                self.connected = True
                startString = "$hello version=%s;" % DB_CONNECTOR_VERSION
                self.comm_socket.send(startString.encode("utf-8"))
                return 0
        except socket.timeout as e:
            logging.warning("Unable to connect to %s. Service is down or not in miltenyi network." % self.host)
            if self.__throwErrors:
                raise Exception("Unable to connect to %s. Service is down or not in miltenyi network." % self.host)
            return -1

    def disconnect(self):
        """Disconnects an active connection the server and resets the object to be able to reestablish a connection"""
        if self.connected:
            self.comm_socket.close()
            self.connected = False
            self.comm_socket = None
            self.dbPort = 0
            self.database = ""

    def sendData(self, start: datetime, end: datetime, result: int, testName: str, testValues: dict,
                 deviceBarcode: miltenyiBarcode.virtualBarcode = None, worker_shortname: str = "") -> int:
        """Method to send data to the server and create all necessary entry's on the database for a valid test to be tracked.

        :param start: datetime object of start of the test :param end: datetime object of the end of the
        test
        :param result: integer that indicates the result of the test. Value should be positive when different
        results have been archived. Value should be negative when an error occurred. When set to 0 the test concluded
        successfully.
        :param testName: the name of the testtype to be written
        :param testValues: a dictionary filed with lists of test results. All lists should have the same length. The name of the entry's are unique and the same as the columns in the database
        :param deviceBarcode: Optional parameter that contains the barcode of the Dut. Should only be set if the proper barcode is used. Object of type miltenyibarcode.
        :param worker_shortname: Optional parameter that contains the login name of the user that produced the test result.
        """
        if not self.connected or not self.valid:  # Only available when connected and connection is valid.
            return -1
        else:
            payload = "$send start=%s end=%s result=%d test=%s" % (
                start.strftime(SQLDATETIMEFORMAT), end.strftime(SQLDATETIMEFORMAT), result, testName)
            if deviceBarcode is not None:
                payload = payload + " device=%s" % deviceBarcode.getBarcodeText()
            if worker_shortname != "":
                payload = payload + " user=%s" % worker_shortname
            if self.testEquipt is not None:
                payload = payload + " testequip=%s" % self.testEquipt
            header = str(list(testValues.keys()))[1:-1].replace("'", "").replace(" ", "")
            payload2 = "%s;" % header
            if type(testValues[list(testValues.keys())[0]]) == list:
                for i in range(len(testValues[list(testValues.keys())[0]])):
                    line = ""
                    for key in testValues.keys():
                        line = line + "%s," % testValues[key][i]
                    payload2 = payload2 + "%s;" % line[:-1]
            else:
                line = ""
                for key in testValues.keys():
                    line = line + "%s," % testValues[key]
                payload2 = payload2 + "%s;" % line[:-1]
            payload = payload + " length=%d;" %(len(payload2))
            if self.debugging:
                print(payload)
                print(payload2)
                return 0
            else:
                self.comm_socket.send(payload.encode("utf-8"))
                if self._readTaskResponse()[0] == 0:
                    self.comm_socket.send(payload2.encode("utf-8"))
                result = self._readTaskResponse()[0]
                return result

    def sendDataNoBarcode(self, start: datetime, end: datetime, result: int, testName: str, testValues: dict,
                          deviceBarcode: str, worker_shortname: str = "") -> int:
        '''
        This method is used to send data for devices that do not use regular Barcodes. Important is to create a unique
        sequence to identify the device.

        :param start: Start of the Test
        :param end: End of the Test
        :param result: The Result as a integer for the specific Test
        :param testName: The Name of the Test
        :param testValues: A dictionary for the specific values.
        :param deviceBarcode: The Barcode or in this case the Name of the Device that has been tested
        :param worker_shortname: The shortname of the worker that tested the Device
        :return: Returns an int that indicates whether the sending succeeded.
        '''
        if not self.connected or not self.valid:  # Only available when connected and connection is valid.
            return -1
        else:
            payload = "$send start=%s end=%s result=%d test=%s" % (
                start.strftime(SQLDATETIMEFORMAT), end.strftime(SQLDATETIMEFORMAT), result, testName)
            if deviceBarcode is not None:
                payload = payload + " device=%s" % deviceBarcode
            if worker_shortname != "":
                payload = payload + " user=%s" % worker_shortname
            if self.testEquipt is not None:
                payload = payload + " testequip=%s" % self.testEquipt
            header = str(list(testValues.keys()))[1:-1].replace("'", "").replace(" ", "")
            payload = payload + ";"
            payload2 = "%s;" % header
            if type(testValues[list(testValues.keys())[0]]) == list:
                for i in range(len(testValues[list(testValues.keys())[0]])):
                    line = ""
                    for key in testValues.keys():
                        line = line + "%s," % testValues[key][i]
                    payload2 = payload2 + "%s;" % line[:-1]
            else:
                line = ""
                for key in testValues.keys():
                    line = line + "%s," % testValues[key]
                payload2 = payload2 + "%s;" % line[:-1]
            payload2 = payload2 + "$$$;"
            if self.debugging:
                print(payload)
                print(payload2)
                return 0
            else:
                start = time.time()
                self.comm_socket.send(payload.encode("utf-8"))
                if self._readTaskResponse()[0] == 0:
                    time1 = time.time()
                    self.comm_socket.send(payload2.encode("utf-8"))
                    result = self._readTaskResponse()[0]
                    print("Time1 = %f and time2 = %f" % (time1 - start, time.time() - time1))
                    return result
                else:
                    logging.warning("Service unable to receive send request" % self.host)
                    if self.__throwErrors:
                        raise Exception("Unable to send data to service")
            # TODO check if ok and return accordingly


    def getTestInTime(self, start: datetime, stop: datetime, testtypeName: str = "", data: bool = False):
        """
        Method that returns all tests that were started in the given timeframe. Needs prior connection to the server.

        :param start: Start of the timeframe in that the tests should be started
        :param stop: Stop of the timeframe in that the tests should be started
        :param testtypeName: Optional parameter that filters the results for a specific testname
        :param data: Optional parameter that adds the testdata to the return parameter. The data is returned as a pandas dataframe. This means that a test with 10 rows of data returns 10 rows with the common infos (start, stop, testtype...) appended to every  row.
        """
        if not self.connected or not self.valid:
            return -1
        else:
            payload = "$tests from=%s to=%s" % (start.strftime(SQLDATETIMEFORMAT), stop.strftime(SQLDATETIMEFORMAT))
            if testtypeName != "":
                payload = payload + " testName=%s" % testtypeName
            if data:
                payload = payload + " data=1"
            payload = payload + ";"
            print(payload)
            self.comm_socket.send(payload.encode("utf-8"))
            # TODO check response and return it later?
            result, data = self._readDataResponse()
            if result == 0:
                return data
            else:
                return result

    def getEventsInTime(self, start: datetime, stop: datetime):
        """
        Method for tracking of service. Returns all events in the given timeframe
        :param start: Start of timeframe
        :param stop: End of timeframe
        :return: Pandas-Dataframe that contains information of the selected events
        """
        if not self.connected or not self.valid:
            return -1
        else:
            payload = "$eventsintime from=%s to=%s" % (
                start.strftime(SQLDATETIMEFORMAT), stop.strftime(SQLDATETIMEFORMAT))

            payload = payload + ";"
            print(payload)
            self.comm_socket.send(payload.encode("utf-8"))
            # TODO check response and return it later?
            result, data = self._readDataResponse()
            if result == 0:
                return data
            else:
                return result

    def getTestTypes(self):
        """
        Method that returns all possible test types
        :return: Pandas dataframe that contains all available test types
        """
        payload = "$Testtypes;"
        self.comm_socket.send(payload.encode("utf-8"))
        result, data = self._readDataResponse()
        if result != 0:
            self.disconnect()
            self.valid = False
            return "Error"
        else:
            return data

    def getLastTests(self, count: int, testName: str):
        """
        Method that returns a quantity of tests of a defined type.
        :param count: Number of tests that should be returned
        :param testName: The name of the test type from which the tests should be selected from.
        :return: Pandas-Dataframe of the test info
        """
        payload = "$LastTests count=%d testName=%s;" % (count, testName)
        self.comm_socket.send(payload.encode("utf-8"))
        result, data = self._readDataResponse()
        if result != 0:
            self.disconnect()
            self.valid = False
            return "Error"
        else:
            return data

    def getFileListFromTest(self,test_guid:str):
        """

        :param test_guid: a test guid in the form of AAAA-BBBB-CCCC
        :return: a pandas dataframe containing the available files. With the file_id you can download the file with the downloadFile() method
        """
        payload = "$getfilelistfromtest test_guid=%s;" % test_guid
        self.comm_socket.send(payload.encode("utf-8"))
        result = self._readDataResponse()
        return result


    def saveFile(self,test_guid:str,filePath:str):
        """
        A function that
        :param test_guid:
        :param filePath:
        :return:
        """
        filename = os.path.basename(filePath)
        if " " in filename:
            raise Exception("Filename should not contain spaces")
        file_size = os.path.getsize(filePath)
        payload = "$savefile test_guid=%s filename=%s length=%d;" % (test_guid,filename,file_size)
        self.comm_socket.send(payload.encode("utf-8"))
        if self._readTaskResponse()[0] == 0:
            self.__send_file(filePath)
            res = self._readTaskResponse()
            print(res)
        else:
            if self.__throwErrors:
                raise Exception("Unable to send file to service")

    def downloadFile(self,file_id:int):
        payload = "$downloadfile file_id=%d;" % file_id
        self.comm_socket.send(payload.encode("utf-8"))
        file = self._readDownloadFileResponse()
        return file

    def _readTaskResponse(self):
        """
        Method that returns an integer that correlates to a certain response from the server
        :return:  0 if successfully and a negative value indication the severity of error.
        """
        data = ""
        while ";" not in data:
            data = data + self.comm_socket.recv(4096).decode("utf-8")  # receive response
        if "Error" in data:
            return -1, data
        elif "NACK" in data:
            return -2, data
        elif "ack" in data:
            return 0, data
        else:
            return 0, data  # Unknown return. Check will be done in underlying function

    def _readDataResponse(self):
        """
        Method that takes a string response and changes the expected data into a pandas dataframe.
        :return: Pandas-Dataframe
        """
        data = ""
        while ";" not in data:
            data = data + self.comm_socket.recv(4096).decode("utf-8")  # receive response
        if "Error" in data:
            return -1, data
        else:
            pos = data.find(":")
            data = data[pos + 1:]
            data = pandas.read_csv(StringIO(data), sep=",")
            last_row = data.iloc[-1]

            if last_row.isna().all() or (last_row.astype(str).str.strip() == ";").any():
                data = data.iloc[:-1]
            return 0, data

    def _readDownloadFileResponse(self):
        data=""
        while ";\r\n" not in data:
            data = data + self.comm_socket.recv(1).decode("utf-8")  # receive response
        if "$downloadfile" in data and "error" not in data:
            expectedLength = int(data.replace(";","").split(":")[-1])
            print(expectedLength)
        else:
            return None
        file = self.__recv_exact(expectedLength)
        return file

    def __recv_exact(self, length: int) -> bytes:
        """Liest exakt n Bytes oder wirft EOFError, wenn die Verbindung vorher endet."""
        buf = bytearray(length)
        view = memoryview(buf)
        got = 0

        while got < length:
            # recv_into schreibt direkt in den Puffer
            r = self.comm_socket.recv_into(view[got:], length - got)
            if r == 0:
                raise EOFError(f"Socket closed early: expected {length}, got {got}")
            got += r

        return bytes(buf)


    def getTestData(self, test_GUID: str):
        """
        Method that takes a specific test GUID and returns the data of the Test
        :param test_GUID: The global unique identifier. This can be previously selected via getTestsInTime() or getLastTests()
        :return: Pandas-Dataframe of the test data
        """
        payload = "$data id=%s;" % test_GUID
        self.comm_socket.send(payload.encode("utf-8"))
        result, data = self._readDataResponse()
        if result == 0:
            return data
        else:
            return "Error"

    def getTestPara(self, testName: str):
        """
        Method that returns the structure of a given Testtype
        :param testName: The name of the testtype
        :return: A Text that include the column names of the test date and the expected data type
        """
        payload = "$para test=%s;" % testName
        self.comm_socket.send(payload.encode("utf-8"))
        result, data = self._readTaskResponse()
        return data

    def announceDeviceWithName(self, mainDevice, subdevice, foundDate: datetime = datetime.now(), slot: str = None):
        """
        A method that is used to safe relation between two devices. This can be used to indicate when a device has been used in the construction of another device
        :param mainDevice: The device that is influenced by the performance of the other device
        :param subdevice: The device that is used for the function of mainDevice
        :param foundDate: The date in which the relation was established
        :param slot: An optional slot parameter. The slot name has to be configured on the database
        :return: 0 if the operation was a success
        """
        payload = "$devicefound device=%s in=%s date=%s" % (
            subdevice, mainDevice, foundDate.strftime(SQLDATETIMEFORMAT))
        if slot is not None:
            payload = payload + " slot=%s" % slot
        payload = payload + ";"
        self.comm_socket.send(payload.encode("utf-8"))
        result, data = self._readTaskResponse()
        return result

    def getMainDevices(self, devices: list):
        """
        A method to get the devices that are influenced by the performance of the given device
        :param devices: a list of devices which mainDevices should be returned
        :return: A pandas dataframe that contain the given devices with their influenced devices. Every device has one row
        """
        deviceList = ','.join(devices)
        payload = "$devicegroup deviceList=%s;" % deviceList
        self.comm_socket.send(payload.encode("utf-8"))
        result, data = self._readDataResponse()
        return data

    # def announceDeviceWithGUID(self, maindevice, subdevice, foundDate: datetime = datetime.now(), slot: str = None):
    #    payload = "$devicefound device=%s in=%s date=%s" % (
    #    subdevice, maindevice, foundDate.strftime(SQLDATETIMEFORMAT))
    #    if slot is not None:
    #        payload = payload + " slot=%s" % slot
    #    payload = payload + ";"
    #    self.comm_socket.send(payload.encode("utf-8"))
    #    result, data = self._readDataResonse()

    def savePicture(self, test_guid, filePath, detailLevel: DetailLevel):
        """
        A function that
        :param detailLevel:
        :param filePath:
        :return:
        """
        filename = os.path.basename(filePath)
        file_size = os.path.getsize(filePath)
        payload = "$saveimage test_guid=%s detail_level=%d filename=%s size=%d;" % (test_guid,detailLevel.value,filename,file_size)
        print(payload)
        self.comm_socket.send(payload.encode("utf-8"))
        self.__send_file(filePath)



    def __send_file(self, filePath):
        # Datei in Chunks senden
        with open(filePath, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                self.comm_socket.sendall(chunk)
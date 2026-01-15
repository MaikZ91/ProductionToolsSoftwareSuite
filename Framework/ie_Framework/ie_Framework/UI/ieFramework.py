#
# -----------------------------------------------------------
# Name: IE_Framework
# Purpose: Basic Framework which does Mundane Tasks and solves them
# Version 0.2.1
# Author: lukasm
#
# Created: 27.07.2022
#
#
#
#
# -----------------------------------------------------------

import shutil
import sys
import time
from abc import abstractmethod
import pandas
from commonIE.pyside_dynamic import loadUi
import glob
import os
from PySide6 import QtWidgets, QtGui, QtCore
from ie_Framework.Utility.configReader import configReader
from ie_Framework.miltenyiBarcode import mBarcodeType
from ie_Framework.UI.processOverview import processOverview
from ie_Framework import miltenyiBarcode
import subprocess

try:
    import httplib  # python < 3.0pip
except:
    import http.client as httplib
FrameworkVersion = "0.8.5"
TIMEFORMAT = "%Y-%m-%d %H:%M:%S"
standardBcQuestion = "Geben sie den Barcode des Testgeräts ein."

def have_internet() -> bool:
    conn = httplib.HTTPSConnection("www.miltenyibiotec.de", timeout=2)
    try:
        conn.request("HEAD", "/")
        return True
    except Exception:
        return False
    finally:
        conn.close()




def serial_ports():
    """ Lists serial port names

        :raises EnvironmentError:
            On unsupported or unknown platforms
        :returns:
            A list of the serial ports available on the system
    """
    import serial
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        # this excludes your current terminal "/dev/tty"
        ports = glob.glob('/dev/tty[A-Za-z]*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/tty.*')
    else:
        raise EnvironmentError('Unsupported platform')

    result = []
    for port in ports:
        try:
            s = serial.Serial(port)
            s.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    return result


def showMessage(Title, Text, InformativeText):
    """This function shows a Message by utilizing a QMessagebox"""
    msgBox = QtWidgets.QMessageBox()
    msgBox.setWindowTitle(Title)
    msgBox.setText(Text)
    msgBox.setInformativeText(InformativeText)
    msgBox.exec_()


def moveWithOverwrite(root_src_dir: str, root_dst_dir: str):
    for src_dir, dirs, files in os.walk(root_src_dir):
        dst_dir = src_dir.replace(root_src_dir, root_dst_dir, 1)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        for file_ in files:
            src_file = os.path.join(src_dir, file_)
            dst_file = os.path.join(dst_dir, file_)
            if os.path.exists(dst_file):
                # in case of the src and dst are the same file
                if os.path.samefile(src_file, dst_file):
                    continue
                os.remove(dst_file)
            shutil.move(src_file, dst_dir)


class Framework(QtWidgets.QMainWindow):

    def __init__(self, parent=None, customWidgets=None, application: QtCore.QCoreApplication = None):
        if application is not None:
            self.app = application
        else:
            try:
                global app
                self.app = app
            except Exception:
                self.app = None
        if parent is not None:
            super(Framework, self).__init__(parent)
        else:
            super(Framework, self).__init__()
        self.conf = configReader("framework.conf")
        uiName = self.conf.getInfo("uiName")
        if uiName is not None:
            self.window = loadUi(uiName, self, customWidgets)
        self.version = self.conf.getInfo("Version")
        if self.version is not None:
            pass
            #self.checkVersion()
        self.addProcesscontrol()
        self.barCode = None

    def addProcesscontrol(self):
        statfound = False
        for m in self.window.menubar.actions():
            curMenu = m.menu()
            if "System" in curMenu.title():
                for a in curMenu.actions():
                    if "Statistic" in a.text():
                        statfound = True
                    else:
                        break
                if not statfound:
                    self.actionStats = QtGui.QAction("Statistic")
                    self.actionStats.setText("Statistic")
                    self.actionStats.triggered.connect(self.checkStatistic)
                    curMenu.addAction(self.actionStats)
        if not statfound:
            curMenu = self.window.menubar.addMenu("System")
            self.actionStats = QtGui.QAction("Statistic")
            self.actionStats.setText("Statistic")
            self.actionStats.triggered.connect(self.checkStatistic)
            curMenu.addAction(self.actionStats)

    def checkVersion(self):
        files = None
        appName = self.conf.getInfo("Applicationname")
        softwareFolder = self.conf.getInfo("Softwarefolder")
        if appName is not None and softwareFolder is not None:
            if softwareFolder.endswith("\\"):
                files = glob.glob(softwareFolder + "*.zip")
            else:
                files = sorted(glob.glob(softwareFolder + "\\" + appName + "*.zip"), key=os.path.getmtime)
            if len(files) == 0:
                return
            versionFolder = str(files[len(files) - 1])
            versionN = versionFolder.split(".")[0]
            versionN = versionN.split("_")
            vLen = len(versionN) - 1
            folderVer = versionN[vLen - 2] + "." + versionN[vLen - 1] + "." + versionN[vLen]
            if self.versionCompare(folderVer):
                showMessage("Update available", "This Programm has an update available. After this Windows closes "
                                                "an Update will take place and the Software will be restarted", "")
                subprocess.Popen(
                    [sys.executable, "updater.py", "-s", versionFolder, "-a", self.conf.getInfo("startApp"), "-b", "1",
                     "-m", self.conf.getInfo("contact")])
                sys.exit(0)
            else:
                pass

    def versionCompare(self, cmpVersion: str, otherVersion: str = None) -> bool:
        """This Function compares the Version of the current programm with a given version. It Returns True when the
         new Version is greater then the current. Otherwise it returns false."""
        if otherVersion is None:
            myVer = self.version.split(".")
        else:
            myVer = otherVersion.split(".")
        newVer = cmpVersion.split(".")
        if myVer[0] == newVer[0] and myVer[1] == newVer[1] and myVer[2] == newVer[2]:
            return False
        if myVer[0] > newVer[0]:
            return False
        elif myVer[1] > newVer[1]:
            return False
        elif myVer[2] > newVer[2]:
            return False
        else:
            return True

    def idle(self, time_ms: int):
        """
        Methode, welche eine vorgegebene Zeit wartet und gleichzeitig die GUI performant hält.
        Wenn andere Funktionen auf der Gui aufgerufen werden, welche länger als die restliche Wartezeit  brauchen, kann
        es vorkommen,dass die Funktion länger wartet als vorgegeben. Daher sollte sie nur bei zeitlich nicht kritischen
        Prozessen verwendet werden.
        """
        if self.app is not None:
            time.sleep(time_ms / 1000)
        start = time.time()
        while (time.time() - start) * 1000 < time_ms:
            self.app.processEvents()

    @abstractmethod
    def loadStatData(self):
        """
        Method that should return a list of Dataframes for each Plotwidget that should be registered in the
        processOverview second return parameter is the restriction list in Form of Highres,highwarn,Lowres, lowWarn
        """
        dataDF = pandas.DataFrame.from_dict(data=self.lastMeasurements)
        return [dataDF], [[self.highRes, self.highWarn, self.lowRes, self.lowWarn]], ["Test"]

    def checkStatistic(self):
            myWindow = QtWidgets.QMainWindow(self.window)
            myWindow.activateWindow()
            plotTest = processOverview(myWindow)
            myWindow.setCentralWidget(plotTest)
            dataDFList, warnList, titleList = self.loadStatData()
            for i in range(len(dataDFList)):
                if warnList is not None:
                    plotTest.addProcessGraph(dataDFList[i], limits=warnList[i], title=titleList[i])
                else:
                    plotTest.addProcessGraph(dataDFList[i])
            # plotTest.paintSelf()
            myWindow.show()

    @abstractmethod
    def updateBarcodeInfo(self):
        pass

    def barcodeAccepted(self, barcode: miltenyiBarcode.mBarcode = None):
        if barcode is None:
            barcode = self.barcode
        appName = self.conf.getInfo("Applicationname")
        if appName == "":
            appName = "IE_Application"
        if self.barcode is None:
            self.setWindowTitle("%s: No barcode" % appName)
        else:
            self.setWindowTitle("%s: %s_%s" % (appName,barcode.getCharge(),barcode.getNumber()))

    def getBarcodeForced(self, checkValid: bool = True, userQuestion: str = standardBcQuestion):
        result, self.barcode = self.getBarcode(checkValid,userQuestion)
        while self.barcode.getBarcodeType() == mBarcodeType.invalid:
            result, self.barcode = self.getBarcode(checkValid,userQuestion)
        if self.barcode.getBarcodeType() == mBarcodeType.unset:
            result = False
        else:
            self.barcodeAccepted()
        return result

    def getBarcode(self, checkValid: bool = True, userQuestion: str = standardBcQuestion):
        userinput, ok = QtWidgets.QInputDialog.getText(self, userQuestion, "Barcode")
        if ok:
            barCode = miltenyiBarcode.mBarcode(userinput)
            if checkValid:
                if barCode.barcodeType == miltenyiBarcode.mBarcodeType.version1:
                    if barCode.getMatNumber() == self.conf.getInfo("expectedMatnumber"):
                        result = True
                    else:
                        result = False
                elif barCode.barcodeType == miltenyiBarcode.mBarcodeType.version2:
                    if barCode.getMatNumber() == self.conf.getInfo("expectedMatnumber") and \
                                            barCode.getGTin() == self.conf.getInfo("expectedGTin"):
                        result = True
                    else:
                        result = False
                else:
                    result = False
            else:
                result = True
        else:
            barCode = miltenyiBarcode.mBarcode("")
            result = False
        return result, barCode


if __name__ == '__main__':
    pass

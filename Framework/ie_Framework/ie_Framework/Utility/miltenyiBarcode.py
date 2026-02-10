import enum
from abc import abstractmethod, ABCMeta, ABC
from datetime import datetime


class mBarcodeType(enum.Enum):
    """
    An Enum that indicates to the user which Barcode type is being tracked
    """
    virtual = -10
    unset = -2
    invalid = -1
    unknown = 0
    version1 = 1
    version2 = 2
    rollingNumber = 10


class virtualBarcode(metaclass=ABCMeta):
    """
    A virtual class that can be used to create new types of barcodes as needed.
    """

    def __init__(self, barcodeText: str):
        """
        Creates a virtual barcode. Should not be called directly.
        :param barcodeText: The raw text of the barcode
        """
        self.__Text = barcodeText
        self.barcodeType = mBarcodeType.virtual

    def getBarcodeText(self):
        """
        Returns the raw barcode text
        :return: The raw barcode text.
        """
        return self.__Text

    @abstractmethod
    def inputBarcode(self, barcode: str):
        """Abstracted Method that has to be implemented. Should process the given barcode"""
        pass

    # Version1: 99991120020579251502111413000040
    # Version2: 010404993400377610624040002117251030210000400091200070501007105
    @staticmethod
    def returnBarcodeType(barcodeText: str) -> mBarcodeType:
        """
        Static method that returns a barcode type based on the given barcode. Static methods can be called without an object
        :param barcodeText: A string filed with the raw barcode
        :return: Returns the type. The result can be interpreted as an Enum or Int
        """
        lenText = len(barcodeText)
        if lenText >= 31:
            if barcodeText[:5] == "99991":  # This can be edited in the future as new versions appear
                return mBarcodeType.version1
            elif barcodeText[0:1] == "01" and lenText > 60:
                return mBarcodeType.version2
            else:
                return mBarcodeType.unknown
        else:
            if lenText == 0:
                return mBarcodeType.unset
            else:
                return mBarcodeType.invalid


class mBarcode(virtualBarcode):
    """
    Class that implements virtualBarcode. Can be used to store a internal barcode that contain serial, material and charge number and starts with 99991
    """

    def __init__(self, barcodeText: str):
        """
        Creates an object of type mBarcode
        :param barcodeText: The raw barcode text that starts with 99991
        """
        super().__init__(barcodeText)
        self.__Text = ""
        self.__chargeNumber = ""
        self.__matNumber = ""
        self.__gTin = ""
        self.__counter = ""
        self.__prodDate = ""
        self._valid = False
        self.barcodeType = mBarcodeType.invalid
        self.inputBarcode(barcodeText)

    def inputBarcode(self, barcodeText: str) -> None:
        """
        Implements virtual method. Dissects the barcode and safes material-,charge- and serial number and safes them in private variables
        :param barcodeText: The barcode to be dissected
        """
        self.barcodeType = self.returnBarcodeType(barcodeText)
        if self.barcodeType != mBarcodeType.invalid and self.barcodeType != mBarcodeType.unset:
            self.__Text = barcodeText
            if self.barcodeType == mBarcodeType.version1:
                self.__matNumber = barcodeText[5: 14]
                self.__chargeNumber = barcodeText[15: 25]
                self.__counter = barcodeText[26:31]
                self._valid = True
            else:
                self.__gTin = barcodeText[2:15]
                self.__chargeNumber = barcodeText[18:27]
                self.__prodDate = barcodeText[30:35]
                self.__counter = barcodeText[38:42]
                self.__matNumber = barcodeText[45:]

    def getBarcodeType(self) -> mBarcodeType:
        """
        Returns the barcode type
        :return:
        """
        return self.barcodeType

    def getValid(self) -> bool:
        """
        returns whether the barcode had a valid format
        :return: True when valid format.
        """
        return self._valid

    def getMatNumber(self) -> str:
        """Returns the material number of the barcode
        :return: String with the material number. Only 9 digits can be processed currently
        """
        return "%s-%s-%s" % (self.__matNumber[0:2], self.__matNumber[3:5], self.__matNumber[6:8])  #TODO test this

    def getCharge(self) -> str:
        """Returns the material number of the barcode"""
        return self.__chargeNumber

    def getGTin(self) -> str:
        """
        Returns the GTIN. Only available in the barcode of devices that are given to customer. Currently unused as a new barcode has to be defined
        :return:
        """
        return self.__gTin

    def getNumber(self) -> str:
        """Returns the serial number"""
        return self.__counter

    def getProdDate(self) -> str:
        """
        Returns the production date. Currently unused as a new barcode has to be defined
        :return:
        """
        return self.__prodDate


class numberBarcode(virtualBarcode):
    """
    A barcode that can be used when internal processes use only a number that resets at the end of a year. It adds the year to the number to make tracking possible
    """

    def __init__(self, barcode: str, constructionDate: datetime):
        """
        Creates an object of this type
        :param barcode: The roling number from the year
        :param constructionDate: A datetime object of the test. The year gets added to the devicebarcode
        """
        super().__init__(barcode)
        self.__year = constructionDate.year
        self.__Text = "%s_%d" % (barcode, constructionDate.year)
        self.barcodeType = mBarcodeType.rollingNumber

    def getBarcodeText(self):
        return self.__Text

    def inputBarcode(self, barcode: str):
        # Inputs a data an appends the manufacturing year to the text.
        self.__Text = "%s_%d" % (barcode, self.__year)

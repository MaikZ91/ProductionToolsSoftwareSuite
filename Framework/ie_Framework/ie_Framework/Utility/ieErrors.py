# DB Errors

class dbException(Exception):
    """Generic type for all errors that appear due to the connection to the ie_Applicationserver"""

class SPCConnectException(dbException):
    """Is thrown if a connection to the SPC appliactionserver failed due to a timeout"""
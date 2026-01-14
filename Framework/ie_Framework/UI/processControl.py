import numpy as np
from PySide6 import QtWidgets, QtGui
import math
import pyqtgraph
import numpy, pandas
from ie_Framework.statMath import calculateCp, calculateCpk

SYMBOLS = ["o", "x", "p", "u"]
PREUNIT = ["p", "n", "u", "m", "", "K", "M", "G", "T"]
styles = {'tickTextWidth': 30, 'autoReduceTextSpace': False, 'autoExpandTextSpace': False}


class qtProcessControl(pyqtgraph.PlotWidget):

    def __init__(self, parent,headline:str = "", unit: str = ""):
        super(qtProcessControl, self).__init__(parent=parent)
        self.unit = unit
        self.data = None
        self.lowRes = None
        self.lowWarn = None
        self.highRes = None
        self.highWarn = None
        self.legend = []
        self.legendBox = None
        self.legendPosX = 40
        self.legendPosY = 15
        self.legendText = ""
        self.setBackground("w")
        self.setMouseEnabled(False,False)
        self.headline = headline
        self.faktor = 0


    def paintEvent(self, ev):
        super(pyqtgraph.PlotWidget, self).paintEvent(ev)
        if self.legendBox is None:
            qp = QtGui.QPainter()
            qp.begin(self.viewport())
            legendTexts = self.legendText.split(";")
            for i in range(len(legendTexts)):
                qp.drawText(self.legendPosX,self.legendPosY + 15*i,legendTexts[i])

    def addHeadline(self):
        pass

    def paintSelf(self):
        self.calculateLegend()
        self.scaleData()
        penWarn = pyqtgraph.mkPen('y', width=2)
        penRes = pyqtgraph.mkPen('r', width=2)
        if self.lowWarn is not None:
            self.addLine(x=None, y=self.lowWarn, pen=penWarn)  # endless horizontal line
        if self.highWarn is not None:
            self.addLine(x=None, y=self.highWarn, pen=penWarn)  # endless horizontal line
        if self.lowRes is not None:
            self.addLine(x=None, y=self.lowRes, pen=penRes)  # endless horizontal line
        if self.highRes is not None:
            self.addLine(x=None, y=self.highRes, pen=penRes)  # endless horizontal line
        if self.data is not None:
            legendIndex = 0
            print(self.data.shape)
            if self.data.shape[1] > 1:
                self.legendBox = self.addLegend()
                for data in self.data[:]:
                    self.paintLine(data, self.legend[legendIndex], SYMBOLS[legendIndex])
                    legendIndex += 1
            else:
                self.paintLine(self.data[:, 0], self.legend[0], SYMBOLS[0])
                # datagraph = self.getPlotItem().items[4]
            self.getPlotItem().getAxis("left").setStyle(**styles)

    def scaleData(self):
        if self.data.shape[1] > 1:
            for data in self.data[:]:
                minVal = np.min(abs(data))
                maxVal = np.max(abs(data))
        else:
            minVal = np.min(abs(self.data[0]))
            maxVal = np.max(abs(self.data[0]))
        if maxVal > 0 and minVal > 0:
            while minVal / math.pow(10,self.faktor + 3) > 1:
                self.faktor += 3
        elif maxVal < 0 and minVal < 0:
            while maxVal / math.pow(10,self.faktor - 3) < 1:
                self.faktor -= 3
        self.data = self.data * pow(10,-self.faktor)
        if self.lowRes is not None:
            self.lowRes = self.lowRes * pow(10,-self.faktor)
        if self.lowWarn is not None:
            self.lowWarn = self.lowWarn * pow(10,-self.faktor)
        if self.highRes is not None:
            self.highRes = self.highRes * pow(10,-self.faktor)
        if self.highWarn is not None:
            self.highWarn = self.highWarn * pow(10,-self.faktor)
        if len(self.unit) > 0:
            self.unit = PREUNIT[math.floor(self.faktor/3) + math.floor(len(PREUNIT)/2)] + self.unit
        elif self.faktor != 0:
            self.unit = "10^%d" % self.faktor
        if len(self.headline) > 0:
            if len(self.unit) > 0:
                self.setTitle("%s in %s" % (self.headline, self.unit))
            else:
                self.setTitle(self.headline)
        else:
            if len(self.unit) > 0:
                self.setTitle("in %s" % self.unit)



    def paintLine(self, data: numpy.array, name: str, symbol):
        symbolBrushs = []
        symbolList = []
        if self.lowRes is None:
            if self.highRes is None:
                symbolBrushs = pyqtgraph.mkBrush(color="g")
                symbolList = "o"
            else:
                for i in range(len(data[:])):
                    if data[i] < self.highRes:
                        if data[i] < self.highWarn:
                            symbolBrushs.append(pyqtgraph.mkBrush(color="g"))
                            symbolList.append("o")
                        else:
                            symbolBrushs.append(pyqtgraph.mkBrush(color="y"))
                            symbolList.append("o")
                    else:
                        symbolBrushs.append(pyqtgraph.mkBrush(color="r"))
                        symbolList.append("+")
        elif self.highRes is None:
            for i in range(len(data[:])):
                if self.lowRes < data[i]:
                    if self.lowWarn < data[i]:
                        symbolBrushs.append(pyqtgraph.mkBrush(color="g"))
                        symbolList.append("o")
                    else:
                        symbolBrushs.append(pyqtgraph.mkBrush(color="y"))
                        symbolList.append("o")
                else:
                    symbolBrushs.append(pyqtgraph.mkBrush(color="r"))
                    symbolList.append("+")
        else:
            for i in range(len(data[:])):
                if self.lowRes < data[i] < self.highRes:
                    if self.lowWarn < data[i] < self.highWarn:
                        symbolBrushs.append(pyqtgraph.mkBrush(color="g"))
                        symbolList.append("o")
                    else:
                        symbolBrushs.append(pyqtgraph.mkBrush(color="y"))
                        symbolList.append("o")
                else:
                    symbolBrushs.append(pyqtgraph.mkBrush(color="r"))
                    symbolList.append("+")
        self.plot(data[:], pen=None, name=name, symbol=symbol, symbolBrush=symbolBrushs)

    def fromNumpy(self, data: numpy.array, name: str):
        self.data = data
        self.legend.append(name)

    def fromDataframe(self, data: pandas.DataFrame):
        self.data = data.to_numpy().transpose()
        self.legend = data.keys()


    def calculateLegend(self):
        self.legendText = ""
        newLegend = []
        if self.data.shape[1] > 1:
            i = 0
            for data in self.data[:]:
                if self.highRes is not None and self.lowRes is not None:
                    cp = calculateCp(data,ueg=self.lowRes,oeg=self.highRes)
                    cpk = calculateCpk(data,ueg=self.lowRes,oeg=self.highRes)
                meanVal = np.mean(data)
                stdVal = np.std(data)
                if len(self.legendText) > 0:
                    self.legendText = self.legendText + ";"
                if self.highRes is not None and self.lowRes is not None:
                    legendSubText = "Cp: %.2f Cpk: %.2f Mean: %2.f Std: %.2f" % (cp,cpk,meanVal,stdVal)
                    self.legendText = self.legendText + legendSubText
                else:
                    legendSubText = "Mean: %2.f Std: %.2f" % (meanVal, stdVal)
                    self.legendText = self.legendText + legendSubText
                newLegend.append(self.legend[i] + " " + legendSubText)
                i += 1
            self.legend  = newLegend
        else:
            if self.highRes is not None and self.lowRes is not None:
                cp = calculateCp(self.data,ueg=self.lowRes,oeg=self.highRes)
                cpk = calculateCpk(self.data,ueg=self.lowRes,oeg=self.highRes)
            meanVal = np.mean(self.data)
            stdVal = np.std(self.data)
            if self.highRes is not None and self.lowRes is not None:
                self.legendText = "Cp: %.2f Cpk: %.2f Mean: %2.f Std: %.2f" % (cp, cpk, meanVal, stdVal)
            else:
                self.legendText = "Mean: %2.f Std: %.2f" % (meanVal, stdVal)



    def lowRestrictions(self, lowRes, lowWarn=None):
        if lowWarn is not None:
            self.lowWarn = float(lowWarn)
        else:
            self.lowWarn = lowWarn
        if lowRes is not None:
            self.lowRes = float(lowRes)
        else:
            self.lowRes = lowRes

    def highRestrictions(self, highRes, highWarn=None):
        if highWarn is not None:
            self.highWarn = float(highWarn)
        else:
            self.highWarn = highWarn
        if highRes is not None:
            self.highRes = float(highRes)
        else:
            self.highRes = highRes


if __name__ == '__main__':
    import sys

    app = QtWidgets.QApplication(sys.argv)
    data = pandas.DataFrame(data=[0.7, 0.91, 0.85, 1.2, 0.4, 0.65, 0.55, 0.98, 0.8, 0.8, 0.62, 1],
                            columns=["last Measurements"])
    myWindow = QtWidgets.QMainWindow(None)
    myWindow.activateWindow()
    plotTest = qtProcessControl(myWindow)
    myWindow.setCentralWidget(plotTest)
    # plotTest.lowRestrictions(0.5, 0.6)
    plotTest.highRestrictions(1, 0.9)
    plotTest.fromDataframe(data)
    plotTest.paintSelf()
    myWindow.show()

    sys.exit(app.exec())


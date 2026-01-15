import math

import pandas
import pyqtgraph

from ie_Framework.UI import processControl
from PySide6 import QtWidgets, QtCore



class MovableLegend(QtWidgets.QFrame):
    """Eine verschiebbare Legende, die über allen Widgets liegt."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            background-color: rgba(255, 255, 255, 200);
            border: 1px solid black;
            border-radius: 6px;
        """)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(4)

        self._drag_pos = None

    def add_entry(self, color, label_text):
        """Fügt einen Eintrag in die Legende hinzu."""
        row = QtWidgets.QHBoxLayout()
        color_box = QtWidgets.QLabel()
        color_box.setFixedSize(16, 16)
        color_box.setStyleSheet(f"background-color: {color}; border: 1px solid black;")
        label = QtWidgets.QLabel(label_text)
        row.addWidget(color_box)
        row.addWidget(label)
        row.addStretch()
        self.layout.addLayout(row)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos:
            diff = event.pos() - self._drag_pos
            self.move(self.pos() + diff)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

class processOverview(QtWidgets.QScrollArea):
    widgetVisible = QtCore.Signal(QtWidgets.QWidget)


    def __init__(self, parent):
        self.connectDots = False
        self.scrollAble = False
        self.clickable = False
        self.showSingleData = True
        self.legend = None
        super(processOverview, self).__init__(parent)
        self.processControlCount = 0
        self.processControls = []
        self.widthWidgets = 2
        self.bootLoader = False
        # self.setLayout(QtWidgets.QGridLayout(self))
        self.contentWidget = QtWidgets.QWidget(self)
        self.setWidgetResizable(True)
        self.setWidget(self.contentWidget)
        # self.layout().addWidget(self.contentWidget)
        self.contentWidget.setLayout(QtWidgets.QGridLayout(self))
        self.setMinSize(self.parent())
        self._watched_widgets = []
        self._visible_widgets = set()
        # Bei jeder Bewegung der Scrollbar prüfen
        self.verticalScrollBar().sliderMoved.connect(self._check_visibility)
        self.horizontalScrollBar().sliderMoved.connect(self._check_visibility)
        #self.widgetVisible.connect(self.checkRepaint)


    def addGlobalLegend(self,content:list):
        self.legend = MovableLegend(self)
        for entry in content:
            self.legend.add_entry(entry[0],entry[1])

    def setMinSize(self, window: QtWidgets.QMainWindow):
        window.setMinimumWidth(400 * self.widthWidgets)
        window.setMinimumHeight(600)

    def addProcessGraph(self, data: pandas.DataFrame, limits=None, title: str = ""):
        newProcessControl = processControl.qtProcessControl(self, title)
        newProcessControl.fromDataframe(data)
        if limits is not None:
            newProcessControl.highRestrictions(limits[0], limits[1])
            newProcessControl.lowRestrictions(limits[2], limits[3])
        cntPC = len(self.processControls)
        self.contentWidget.layout().addWidget(newProcessControl,math.floor(cntPC / self.widthWidgets),cntPC % self.widthWidgets)
        self.processControls.append(newProcessControl)
        self.processControls[cntPC].paintSelf()

    def addHeadlines(self):
        for graph in self.processControls:
            graph.addHeadline()

    def addExplanation(self):
        pass

    def addMeasurementGraph(self,newGraph: pyqtgraph.PlotWidget):
        cntPC = len(self.processControls)
        self.contentWidget.layout().addWidget(newGraph,math.floor(cntPC / self.widthWidgets),cntPC % self.widthWidgets)
        self.processControls.append(newGraph)
        #self.processControls[cntPC].paintSelf()

    def setWidthWidgets(self, newNumber: int):
        if newNumber < 1:
            return
        else:
            i = 0
            self.widthWidgets = newNumber
            for processC in self.processControls:
                processC.move(i % self.widthWidgets, math.floor(i / self.widthWidgets))
                i += 1

    def _check_visibility(self):
        #print("Signal emit")
        for widget in self._watched_widgets:
            if self._is_widget_visible(widget):
                if widget not in self._visible_widgets:
                    self._visible_widgets.add(widget)
                    self.widgetVisible.emit(widget)
            else:
                self._visible_widgets.discard(widget)  # ggf. zurücksetzen, um erneut sichtbar zu melden

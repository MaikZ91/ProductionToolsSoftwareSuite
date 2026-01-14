#
# -----------------------------------------------------------
# Name: configReader
# Purpose: Basic Configreader which lets you read and access a .conf file
# Version 0.2
# Author: lukasm
#
# Created: 27.07.2022
#
#
#
#
# -----------------------------------------------------------

import string
import ie_Framework.Utility.ieErrors

class configReader:

    def __init__(self, configFile: str, listFields: int = 1):
        self.content = {}
        f = open(configFile, "r")
        for line in f:
            line = line.split("#")[0]  # ermöglichen eines Kommentars in der Configdatei
            cont = line.replace("\n", "").split("=")
            if len(cont) == 2:
                if len(cont[1].split(";")) > 1:
                    liste = cont[1].split(";")
                    for i in range(len(liste)):
                        liste[i] = liste[i].strip()
                    while len(liste) < listFields:
                        liste.append("")
                    self.content[cont[0].strip()] = liste
                else:
                    if listFields != 1:
                        liste = [cont[1].strip()]
                        while len(liste) < listFields:
                            liste.append("")
                        self.content[cont[0].strip()] = liste
                    else:
                        self.content[cont[0].strip()] = cont[1].strip()
            else:
                pass  # Wenn kein = vorhanden ist, handelt es sich um eine Infozeile

    def getInfo(self, header: str):
        if header in self.content.keys():
            return self.content[header]
        else:
            return None

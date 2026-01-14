#
# -----------------------------------------------------------
# Name: updater
# Purpose: python script which updates the current Version of Software it is attached to. No conditions are checked
# before Update so the user is responsible to determin the need to update
# Version 0.1
# Author: lukasm
#
# Created: 27.07.2022
#
#
#
#
# -----------------------------------------------------------
import glob
import os
import shutil
import sys
# from configReader import configReader
from argparse import ArgumentParser
import zipfile
import subprocess
import smtplib
from email.mime.text import MIMEText
import platform


def moveWithOverwrite(root_src_dir: str, root_dst_dir: str):
    # root_src_dir = 'Src Directory\\'
    # root_dst_dir = 'Dst Directory\\'

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


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument("-s", "--source", dest="source", default=None, type=str)
    parser.add_argument("-a", "--applicationfile", dest="appStart", default=None, type=str)
    parser.add_argument("-b", "--backup", dest="backup", default=0, type=int)
    parser.add_argument("-m", "--maintanance", dest="maintanance", default="", type=str)
    args = parser.parse_args()
    source = args.source
    mypath = os.getcwd()
    if source is not None:
        tempPath = mypath + "_temp\\"
        with zipfile.ZipFile(source, 'r') as zip_ref:
            zip_ref.extractall(tempPath)
        tempFilesChecker = glob.glob(tempPath + "*")
        requirementsNotMet = False
        if len(tempFilesChecker) > 1:
            requireNewFile = tempPath + "requirements.txt"
        else:
            requireNewFile = tempFilesChecker[0] + "\\requirements.txt"
        try:  # we will determin if we can do an update. This depends on whether we have the same requirements than
            # before in case of an requirement missing we need elevated rights in order to istall them. When this is
            # needed we inform the responsible person about this.
            newReq = {}
            oldReq = {}
            missingReq = ""
            requirementsOld = open(mypath + "\\requirements.txt")
            requirementsNew = open(requireNewFile)
            for line in requirementsNew:
                req = line.split("~=")
                if len(req) == 2:
                    newReq[req[0]] = req[1]
            for line in requirementsOld:
                req = line.split("~=")
                if len(req) == 2:
                    oldReq[req[0]] = req[1]
            for i in newReq.keys():
                if i in oldReq:
                    if oldReq[i] == newReq[i]:
                        pass
                    else:
                        missingReq = missingReq + i + "~=" + newReq[i] + " is not up to date\n"
                else:
                    missingReq = missingReq + i + "~=" + newReq[i] + " is missing\n"
            requirementsOld.close()
            requirementsNew.close()
            if missingReq != "":  # When Requirements differ we send an email to the responsible person
                requirementsNotMet = True
                print("Test")
                msg = MIMEText(platform.node() + ":\n" + missingReq)
                msg['Subject'] = platform.node() + " update not possible!"
                msg['From'] = "lukasm@miltenyi.com"
                msg['To'] = args.maintanance
                s = smtplib.SMTP_SSL('localhost',465)
                s.sendmail(msg["From"], msg["To"], msg.as_string())
                s.quit()
        except OSError as e:
            print(e.strerror + "\n")

        if not requirementsNotMet:
            if args.backup == 1:
                archivePath = mypath + "_archive\\"
                if os.path.isdir(archivePath):
                    shutil.rmtree(archivePath)
                # os.mkdir(archivePath)
                shutil.copytree(mypath, archivePath)

            if len(tempFilesChecker) > 1:
                moveWithOverwrite(tempPath, mypath + "\\")
            else:
                moveWithOverwrite(tempFilesChecker[0], mypath + "\\")
            shutil.rmtree(tempPath)
            if args.appStart is not None:
                subprocess.Popen([sys.executable, args.appStart])
                sys.exit(0)

    # reader = configReader("framework.conf")

#!/usr/bin/python

import sys
import math
import time
import json
import pigpio
import socket
import urllib
import random
import datetime
import threading
import SocketServer
from os import listdir
from os.path import isfile, join
from ButtonController import ButtonController

# - - - - - - - - - - - - - - - - 
# - - - - SOCKET CLASSES  - - - -
# - - - - - - - - - - - - - - - -
class ThreadedTCPRequestHandler(SocketServer.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(1024)
        ledCtrl.updateStatus(data)
        response = ledCtrl.getStatus() 
        self.request.sendall(response)

class ThreadedTCPServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    pass

# - - - - - - - - - - - - - - - - 
# - - LED CONTROLLER CLASS  - - -
# - - - - - - - - - - - - - - - -
class LEDController:
    def __init__(self):
        self.pi1 = pigpio.pi('localhost', 8888)
        self.buttonController = ButtonController()
        self.pinList = [14, 15, 18]
        self.pi1.set_mode(4, pigpio.INPUT)
        self.pi1.set_pull_up_down(4, pigpio.PUD_OFF)
        self.queryString = ""
        self.statusDict = {'color': [0,0,0],    # 0 ~ 255 rgb
                        'mode': 0,          # 0 -> constant on, 1 -> blinking, 2: asynchron blinking 
                      'period': 1000,       # in milliseconds
                      'repeat': 0,          # if x > 0 -> stop after blinking x times 
                         'ack': 1,          # was the current alarm acknowledged? 0 -> NO, 1 -> YES
                        'json': 0,          # 0 -> status response in HTML, 1 -> status response in Json
                        'info': "",         # info
                        'remote_addr': 0,   # Where was the request sent from?
                        'remote_host': 0}   # What is the name of the request sender?
        self.listOfKeys = ['color', 'period', 'repeat', 'mode', 'ack', 'json', 'info']
        self.explanationDict = {'color': "rgb values from 0 ~ 255", 
                        'period': "length of blinking period (in millisecs)",
                        'repeat': "if x > 0 -> stop after blinking x times",
                          'mode': "0-> ON, 1-> blinking, 2-> blinking asynchronously",
                           'ack': "parameter to acknowledge an alarm / blinking pattern",
                          'json': "0 -> status response in HTML, 1 -> status response in Json",
                          'info': "information about the alarm"}
        self.logList = []
        self.brightness = self.getBrightnessSetting()
        self.setupPWM()
        self.resetUpdateParaMode1()
        self.resetUpdateParaMode2()
        self.newStatusFlag = True;
        self.argList = []
    def updateStatus(self, query_string):
        self.newStatusFlag = True;
        self.getLogData = False;
        self.getDropDownData = False;
        self.settingUpButtons = False;
        self.settingUpSettings = False;
        colorWasSet = False
        self.queryString = query_string
        self.argList=query_string.split('&')
        for arg in self.argList:                 
            if arg is not "":
                key, value=arg.split('=')       
                key = key.lower()
                if key == 'ack':
                    if int(value) != 0:
                        self.statusDict['ack'] = 1
                        # an ack was sent. This means we have to set all the acks in the logList!
                        self.setAcksInLogList()
                    else:
                        self.statusDict['ack'] = 0
                elif key == 'deletelog':
                    if int(value) != 0:
                        self.deleteLog()
                elif key == 'getlogdata':
                    if int(value) != 0:
                        self.getLogData = True
                elif key == 'getdropdowndata':
                    if int(value) != 0:
                        self.getDropDownData = True
                elif key == 'settingupbuttons':
                    if int(value) != 0:
                        self.settingUpButtons = True
                elif key == 'settingupsettings':
                    if int(value) != 0:
                        self.settingUpSettings = True
                elif key == 'json':
                    if int(value) != 0:
                        self.statusDict['json'] = 1
                    else:
                        self.statusDict['json'] = 0
                elif key == 'color':
                    colorWasSet = True
        if  colorWasSet:   # Only load the other parameters if at least 1 color parameter was sent  
            self.resetStatusDict()                        
            for arg in self.argList:
                key, value=arg.split('=')
                key = key.lower()
                if key in self.statusDict:
                    if key == 'color':
                        valueArr = urllib.unquote(value).split(',')
                        for index, element in enumerate(valueArr):
                            self.statusDict['color'][index] = int(element) 
                    else:
                        try:                # Test weather or not the thing can be converted to an int
                            self.statusDict[key] = int(value)
                        except ValueError:
                            self.statusDict[key] = value
            self.checkBoundries()
            clonedDict = dict(self.statusDict)
            clonedDict['date'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logList.insert(0, clonedDict)
            if len(self.logList) > 500:
                self.logList.pop()  #delete last item from list
            self.resetUpdateParaMode1()
            self.resetUpdateParaMode2()
    def constantOn(self):
        if self.newStatusFlag:
            for index, pin in enumerate(self.pinList):
                self.pi1.set_PWM_dutycycle(pin, self.statusDict['color'][index])
            self.newStatusFlag = False
        # sleep for 100ms
        time.sleep(0.1)
    def blinking(self):
        if self.stepCounter < 255:
            self.stepCounter += 1
        else:
            self.halfPeriodCounter += 1
            self.periodCounter = self.halfPeriodCounter / 2
            if self.statusDict['repeat'] > 0 and self.periodCounter >= self.statusDict['repeat']:
                self.repeatEnded = True
            self.stepCounter = 0
            self.risingEdge = not self.risingEdge 
        for index, pin in enumerate(self.pinList):
            if self.risingEdge:
                self.pi1.set_PWM_dutycycle(pin, int(self.statusDict['color'][index]*
                    (math.cos(self.stepCounter/255.0*math.pi - math.pi)/2.0 + 0.5)))
            else:
                self.pi1.set_PWM_dutycycle(pin, int(self.statusDict['color'][index] - 
                    self.statusDict['color'][index]*(math.cos(self.stepCounter/255.0*math.pi - math.pi)/2.0 + 0.5)))
        time.sleep(self.stepDuration / 1000.0)         
    def asynchBlinking(self):
        for index, pin in enumerate(self.pinList):
            if self.getTimeInMilliSec() > self.oldTimeM2[index] + self.stepDurationM2[index]:
                self.oldTimeM2[index] = self.getTimeInMilliSec()
                if self.stepCounterM2[index] < 255:
                    self.stepCounterM2[index] += 1
                else:
                    self.stepCounterM2[index] = 0
                    self.risingEdgeM2[index] = not self.risingEdgeM2[index] 
                if self.risingEdgeM2[index]:
                    self.pi1.set_PWM_dutycycle(pin, int(self.statusDict['color'][index]*
                        (math.cos(self.stepCounterM2[index]/255.0*math.pi - math.pi)/2.0 + 0.5)))
                else:
                    self.pi1.set_PWM_dutycycle(pin, int(self.statusDict['color'][index] - 
                        self.statusDict['color'][index]*(math.cos(self.stepCounterM2[index]/255.0*math.pi - math.pi)/2.0 + 0.5)))
        # sleep for 2ms
        time.sleep(0.002)
    def update(self):
        self.buttonController.update(self.pi1.read(4), self.statusDict['ack'])
        if self.statusDict['ack'] != 0 or self.repeatEnded:
            if self.newStatusFlag:
                self.resetLEDs()
                self.newStatusFlag = False
            time.sleep(0.1)
        else:
            if self.statusDict['mode'] == 0: 
                self.constantOn()
            elif self.statusDict['mode'] == 1:
                self.blinking()
            elif self.statusDict['mode'] == 2:
                self.asynchBlinking()
            else:
                # sleep for 100ms if there's nothing to do
                time.sleep(0.1)
    def getTimeInMilliSec(self):
        return int(time.time()*1000)
    def resetStatusDict(self):
        self.statusDict['color'] = [0,0,0]
        self.statusDict['period'] = 1000
        self.statusDict['ack'] = 0
        self.statusDict['repeat'] = 0 # don't repeat unless explicitly told to do so
        self.statusDict['json'] = 0
        self.statusDict['info'] = ""
    def resetLEDs(self):
        for pin in self.pinList:
            self.pi1.set_PWM_dutycycle(pin, 0)
    def resetUpdateParaMode1(self):
        self.halfPeriodCounter = 0
        self.periodCounter = 0
        self.repeatEnded = False
        self.stepDuration = self.statusDict['period'] / 510.0
        self.stepCounter = 0
        self.risingEdge = True
    def getStatus(self):
        if self.getLogData:
            # Here we need to return a nicely formatted table!
            # something like this:
            return self.getTableHTML()
        elif self.getDropDownData:
            # Here we need to return some Bootstrap Dropdown menus!
            return self.getDropDownHTML()
        elif self.settingUpButtons:
            # This is the area where we manage the buttonSettings.json file. 
            # we do not even need to return anything.
            self.setButtonSettings()
            return ""
        elif self.settingUpSettings:
            # This is the erea where we manage the Settings.json file.
            # we do not even need to return anything.
            self.setSettings()
            self.brightness = self.getBrightnessSetting()
            self.setupPWM()
            return ""
        else:
            if self.statusDict['json'] == 0:
                argList = ""
                argExplanation = ""
                clonedDict = dict(self.statusDict)
                for key in self.listOfKeys:
                    argList += key + ": " + str(self.statusDict[key]) + "<br>\r\n"
                for key in self.listOfKeys:
                    argExplanation += "<b>" + key + ":</b> " + self.explanationDict[key] + "<br>\r\n"
                response = "<h2>Argument list</h2>\r\n" + argList + \
                "\r\n<h2>Argument Explanation</h2>\r\n" + argExplanation 
                return response 
            else:
                return json.dumps(self.statusDict)
    def deleteLog(self):
        self.logList = []
    def getTableHTML(self):
        html = ""
        html +=''' <table class="table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>IP Address</th>
                        <th>Parameter</th>
                        <th>Info</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>'''
        for ent in self.logList:
            info = urllib.unquote(str(ent['info']))  
            argList = ""
            for key in self.listOfKeys:
                argList += key + ": " + urllib.unquote(str(ent[key])) + "<br>\r\n"
            html += '<tr class="{0}">'.format("danger" if (ent['ack'] == 0) else "success")
            html += "<td>" + ent['date'] + "</td>"
            html += "<td>" + ent['remote_addr'] + "</td>"
            html += '''<td><a href="javascript://" title="Parameter" data-toggle="popover" data-placement="right"
                         data-html="true" data-content="''' + argList + '">color=' + \
                         str(ent['color'][0]) + "," + str(ent['color'][1]) + "," + str(ent['color'][2]) + '...</a></td>'
            if info == "":
                html += "<td></td>" 
            elif len(info) <= 9:
                # in case the info text is quite small we don't need to add to the end of the string "..."
                html += '''<td><a href="javascript://" title="Info" data-toggle="popover" data-placement="right"
                            data-html="true" data-content="''' + info  + '">' + info  + '</a></td>'
            else:
                cutOffCor = self.getStringCutOffCorVal(info)
                html += '''<td><a href="javascript://" title="Info" data-toggle="popover" data-placement="right"
                            data-html="true" data-content="''' + info  + '">' + info[:9+cutOffCor]  + '...</a></td>'
            if ent['ack'] == 0:
                html += "<td>pending</td></tr>"
            else:
                html += "<td>acknowledged</td></tr>"
        # and in the end, we need to get the footer thing. 
        html += '''</tbody>
                  </table>
                  <script>
                    $('[data-toggle="popover"]').popover();
                  </script>'''
        return html
    def getDropDownHTML(self):
        keyList = ['dropdown1', 'dropdown2', 'dropdown3', 'dropdown4']
        htmlList = []
        buttonScriptFileNames = self.getButtonScriptNames()
        buttonScriptFileNames.append("Do Nothing")
        settings = self.getButtonSettings() 

        for ent in keyList:    # There are 4 DropDown Buttons.
            html =  ''' <div class="dropdown">
                            <button class="btn btn-default dropdown-toggle" type="button" data-toggle="dropdown" name="dropDown1">'''
            html +=         urllib.unquote(settings[ent]) + '''<span class="caret"></span></button>
                            <ul class="dropdown-menu">'''
            for entry in buttonScriptFileNames:
                html +=     '<li><a href="#">' + entry + '</a></li>'

            html += '''     </ul>
                        </div>'''
            htmlList.append(html)
        return json.dumps(htmlList) 
    def setAcksInLogList(self):
        for ent in self.logList:
            if 'ack' in ent:
                ent['ack'] = 1
    def resetUpdateParaMode2(self):
        durTmp = self.statusDict['period'] / 510.0
        self.stepDurationM2 = [int((random.random()-0.5)*durTmp + durTmp), 
                               int((random.random()-0.5)*durTmp + durTmp),
                               int((random.random()-0.5)*durTmp + durTmp)]
        self.stepCounterM2 = [0,0,0]
        self.oldTimeM2 = [0,0,0]
        self.risingEdgeM2 = [True, True, True]
    def setupPWM(self):
        for pin in self.pinList:
            self.pi1.set_PWM_frequency(pin,600)
            self.pi1.set_PWM_range(pin, 255 + int(745*(255-self.brightness)/255.0))  #1000
            self.pi1.set_PWM_dutycycle(pin, 0)
    def checkBoundries(self):
        for index, _ in enumerate(self.pinList):
            if self.statusDict['color'][index] > 255:
                self.statusDict['color'][index] = 255
            elif self.statusDict['color'][index] < 0:
                self.statusDict['color'][index] = 0
    def getStringCutOffCorVal(self, string):
        notASCIICounter = 0
        cutOffCor = 0
        for i in range(0,9):
            try:
                string[i].decode('ascii')
            except:
                notASCIICounter += 1
        tmp = notASCIICounter%3
        cutOffCor = 3-tmp if tmp>0 else tmp 
        return cutOffCor
    def getButtonScriptNames(self):
        onlyfiles = [f for f in listdir("/usr/local/bin/ButtonScripts") if isfile(join("/usr/local/bin/ButtonScripts", f))]
        return onlyfiles
    def setButtonSettings(self):
        keyList = ['dropdown1', 'dropdown2', 'dropdown3', 'dropdown4']
        # settings contains the current ButtonSettings.json data
        settings = self.getButtonSettings()
        for arg in self.argList:                 
            if arg is not "":
                key, value = arg.split('=')      
                key = key.lower()
                for ent in keyList:
                    if key == ent: 
                        settings[ent] = value
        with open('/usr/local/bin/ButtonSettings.json', 'w+') as outfile:
                json.dump(settings, outfile)
    def getButtonSettings(self):
        if not isfile("/usr/local/bin/ButtonSettings.json"):
            buttonSettingsInit = {'dropdown1': "Do Nothing",
                                  'dropdown2': "Do Nothing",
                                  'dropdown3': "Do Nothing",
                                  'dropdown4': "Do Nothing"}
            with open('/usr/local/bin/ButtonSettings.json', 'w+') as outfile:
                    json.dump(buttonSettingsInit, outfile)
        with open('/usr/local/bin/ButtonSettings.json') as data:
            return json.load(data)
    def getSettings(self):
        if not isfile("/usr/local/bin/Settings.json"):
            SettingsInit = {'brightness': 60}
            with open('/usr/local/bin/Settings.json', 'w+') as outfile:
                    json.dump(SettingsInit, outfile)
        with open('/usr/local/bin/Settings.json') as data:
            return json.load(data)
    def getBrightnessSetting(self):
        settingsDict = self.getSettings()
        tmp = settingsDict['brightness']
        if tmp <= 255 and tmp >= 0:
            return tmp
        elif tmp > 255:
            return 255
        else:
            return 0
        return settingsDict['brightness']
    def setSettings(self):
        # sets one Settings entry (parameter-value pair in self.argList)
        keyList = ['brightness']
        # settings contains the current Settings.json data
        settings = self.getSettings()
        for arg in self.argList:                 
            if arg is not "":
                key, value = arg.split('=')      
                key = key.lower()
                for ent in keyList:
                    if key == ent: 
                        try: # Test weather or not the thing can be converted to an int
                            settings[ent] = int(value)
                        except ValueError:
                            # we expect the all the entries in Settings.json to be convertable to int
                            # if it isn't, we do nothing
                            pass
        with open('/usr/local/bin/Settings.json', 'w+') as outfile:
                json.dump(settings, outfile)



# - - - - - - - - - - - - - - - - - 
# SETTING UP SOCKET & CONTROLLER  -
# - - - - - - - - - - - - - - - - -
# Socket
server = ThreadedTCPServer(('localhost', 7777), ThreadedTCPRequestHandler, False)
server.allow_reuse_address = True
server.server_bind()     
server.server_activate() 
server_thread = threading.Thread(target=server.serve_forever)
server_thread.daemon = True
server_thread.start()
# LEDController
ledCtrl = LEDController()


# - - - - - - - - - - - - - - - - 
# - - - - - MAIN LOOP - - - - - -
# - - - - - - - - - - - - - - - -
while True:
    try:
        ledCtrl.update()
    except(KeyboardInterrupt, SystemExit):
        server.shutdown()
        server.server_close()
        raise
    except:
        raise


# - - - - - - - - - - - - - - - - 
# - - - - - - MEMO  - - - - - - -
# - - - - - - - - - - - - - - - -

# As you probaply can see, we got a brightness parameter here.
# now we need to check for incoming strings. If there is 
# some brightness nerd we need to write the val into the file
# But when should we s

#!/usr/bin/env python3
# -*- coding: latin-1 -*-

#/*                          SERIAL COMMANDER
#*                A simple serial interface to send commands
#*
#*      Copyright 2013, Carlos Gonzalez Cortes, carlgonz@ug.uchile.cl
#*
#* This program is free software: you can redistribute it and/or modify
#* it under the terms of the GNU General Public License as published by
#* the Free Software Foundation, either version 3 of the License, or
#* (at your option) any later version.
#*
#* This program is distributed in the hope that it will be useful,
#* but WITHOUT ANY WARRANTY; without even the implied warranty of
#* MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#* GNU General Public License for more details.
#*
#* You should have received a copy of the GNU General Public License
#* along with this program.  If not, see <http://www.gnu.org/licenses/>.
#*/

import sys
import datetime
import json
import re
import os

from pymongo import MongoClient

from PyQt4.Qt import *
from PyQt4 import QtGui
from PyQt4 import QtCore

from forms.Console import Ui_MainWindow
from forms.EditCommandDialog import Ui_DialogEditCommandList

from client import Client
from telemetry import Telemetry


config_path = "/usr/share/groundstation/"
if not os.path.exists(config_path):
    config_path = "/usr/local/share/groundstation/"
    if not os.path.exists(config_path):
        config_path = "config/"
    else:
        print("Warning: no application path found")


class SerialCommander(QtGui.QMainWindow):
    """
    Main class, creates and configures main window. Also sets signals and
    slots.
    """
    #Signals
    _new_char = pyqtSignal(type(""))  # New char signal
    
    def __init__(self):
        QtGui.QMainWindow.__init__(self)

        #Instance variables
        self.client = None
        self.alive = False
        self.receiver_thread = None
        self.history = []
        self.history_cnt = 0
        self.timestamp = False
        self.put_timestamp = True
        self.mongo_client = MongoClient('localhost', 27017)

        #Load config
        try:
            config_file = open(config_path + "config.json", 'r')
            self.config = json.load(config_file)
            config_file.close()
        except IOError:
            self.config = {}

        #Load telecommands list
        try:
            tc_set = set()  # Avoid duplicated items
            tc_file = open(config_path + "cmd_list.txt", 'r')

            for line in tc_file:
                line = line.replace(',', ', ')
                line = line.replace('\n', '')
                tc_set.add(line)

            tc_file.close()
            
            tc_list = list(tc_set)
            tc_list.sort()
            self.config["tc_list"] = tc_list

        except IOError as e:
            print(e)
            self.config["tc_list"] = []

        self.commands_list = self.config.get("commands", [])

        #Set GUI
        self.window = Ui_MainWindow()
        self.window.setupUi(self)
        self.setup_comm()
        self.setup_send()
        self.setup_actions()
        self.setup_telecommands()
        
        #Set Telemetries to be stored
        self.telemetries = []
        
    def setup_comm(self):
        """
        Sets connections combobox.
        """
        #Connections
        self.window.pushButtonOpenPort.clicked.connect(self.open_connection)
        self.window.pushButtonClosePort.clicked.connect(self.close_connection)

        #Hosts
        available_hosts = self.config.get("hosts", ["", ])
        self.window.lineEditURL.setText(available_hosts[0])

        #Ports
        available_ports_send = self.config.get("ports_send", ["", ])
        available_ports_send.reverse()
        self.window.comboBoxPortSend.addItems(available_ports_send)
        available_ports_recv = self.config.get("ports_recv", ["", ])
        available_ports_recv.reverse()
        self.window.comboBoxPortRecv.addItems(available_ports_recv)

    def setup_send(self):
        """
        Config cmd send and selection window
        """
        #Add command list
        self.window.listWidgetCommand.addItems(self.commands_list)
        
        #Conexiones
        self.window.listWidgetCommand.itemDoubleClicked.connect(self.command_clicked)
        self.window.pushButtonSend.clicked.connect(self.send_msg)
        self.window.checkBoxTimestamp.toggled.connect(self.timestamp_toggle)
    
    def setup_actions(self):
        """
        Configura los menus de la barra de herramientas
        """
        self.window.actionGuardar.triggered.connect(self.save_log)
        self.window.actionAgregar_comando.triggered.connect(self.add_cmd)
        # self.client.new_message.connect(self.write_terminal)

    def setup_telecommands(self):
        """
        Configures telecommands tab
        """

        #Fill telecommand list
        cmd_list = self.config.get("tc_list")
        if cmd_list:
            self.window.listWidget_cmd.addItems(cmd_list)

        #Connections
        self.window.lineEdit_tcfilter.textChanged.connect(self.tc_filter)
        self.window.listWidget_cmd.itemDoubleClicked.connect(self.tc_addtoframe)
        self.window.pushButton_tcclear.clicked.connect(self.tc_clearframe)
        self.window.pushButton_tcsend.clicked.connect(self.tc_send)
        self.window.pushButton_tcsave.clicked.connect(self.tc_save)

    def tc_filter(self, text):
        """
        Filters telecommand list by text
        """
        if len(text):
            f_list = [s for s in self.config.get("tc_list") if str(text) in s]
            self.window.listWidget_cmd.clear()
            self.window.listWidget_cmd.addItems(f_list)
        else:
            self.window.listWidget_cmd.clear()
            self.window.listWidget_cmd.addItems(self.config.get("tc_list"))

    def tc_addtoframe(self, item):
        """
        Add new tc in item to current frame
        """
        tc_text = str(item.text())
        [cmd, name] = tc_text.split(',')
        item_cmd = QTableWidgetItem(cmd)
        item_name = QTableWidgetItem(name)
        item_par = QTableWidgetItem('0')

        rows = self.window.tableWidget_tcframe.rowCount()
        self.window.tableWidget_tcframe.setRowCount(rows+1)
        self.window.tableWidget_tcframe.setItem(rows, 0, item_name)
        self.window.tableWidget_tcframe.setItem(rows, 1, item_cmd)
        self.window.tableWidget_tcframe.setItem(rows, 2, item_par)

    def tc_clearframe(self, item):
        """
        Clears current tc frame
        """
        self.window.tableWidget_tcframe.clearContents()
        self.window.tableWidget_tcframe.setRowCount(0)

    def tc_send(self):
        """
        Send current tc frame
        """
        tc_frame = []
        cmd_stop = 0xfffe
        rows = self.window.tableWidget_tcframe.rowCount()

        for i in range(rows):
            item = self.window.tableWidget_tcframe.item(i, 1)
            tc = int(str(item.text()), 16)
            tc_frame.append(tc)

            item = self.window.tableWidget_tcframe.item(i, 2)
            value = int(str(item.text()), 10)
            tc_frame.append(value)

        tc_frame.append(cmd_stop)
        tc_frame.append(cmd_stop)

        tc_json = {"type": "tc",
                   "data": tc_frame}

        tc_msj = json.dumps(tc_json)
        self.client.send(tc_msj)

    def timestamp_toggle(self, value):
        """
        Slot que intercambia entre agregar o no la marca de tiempo
        @deprecated
        """
        self.timestamp = value
        
    def add_cmd(self):
        """
        Edita la lista de comandos
        """
        dialog = EditCommandDialog(self,self.commands_list)
        self.commands_list = dialog.run_tool()
        
        self.window.listWidgetCommand.clear()
        self.window.listWidgetCommand.addItems(self.commands_list)
    
    def write_terminal(self, text):
        """
        Log received msg to terminal
        """
        text = text.replace('\n', '')
        text = text.replace('\r', '')
        
        #Separate msg fields. If fail, just copy the text
        try:
            msg = json.loads(text)
            typ = msg.get("type", "other")
            data = msg.get("data", text)
            log = "[{0}] {1}\n".format(typ, data)
        except:
            typ = "debug"
            data = text
            log = "[{0}] {1}\n".format(typ, data)
        
        #Moves cursor to end
        c = self.window.textEditTerminal.textCursor()
        c.movePosition(QTextCursor.End)
        self.window.textEditTerminal.setTextCursor(c)
        
        if self.timestamp:
            #Add timestamp
            ts = datetime.datetime.now().isoformat(' ')
            log = '[{0}]{1}'.format(ts, log)

        #Normal mode, just write text in terminal
        self.window.textEditTerminal.insertPlainText(log)
        
        c.movePosition(QTextCursor.End)
        self.window.textEditTerminal.setTextCursor(c)

        #Process special msgs
        if typ == "tm":
            self._process_tm(data)    

    def _process_tm(self, data):
        ts = datetime.datetime.now().strftime("%y-%m-%d %H:%M:%S")
        data = data.split(',')
        t_frame = data[0] # type of frame
        n_frame = data[1] # number of frame
        t_data = data[2] # type of telemetry payload (only if it is a frame of type 0x100 or 0x400)
        l_data = data[3] # length of data (only if it is a frame of type 0x100 or 0x400)
        p_status = data[4] # payload status (only if it is a frame of type 0x100 or 0x400)
        _data = data[5:] # data for frames 0x100 or 0x400
        __data = [data[3], data[4] ] + _data # data for other frames 

        line = "({4})[{0}][{1}]-[{2}] {3}".format(t_frame, n_frame, t_data, __data, ts)
        #self.window.listWidgetTelemetry.addItem(line)
        
        
        if t_frame == "0x0100" or t_frame == "0x0400": #it is a new frame o 0x400
            
            ## First check if the last telemetry is alright
            if len(self.telemetries) != 0:
                #check if the last telemetry is finished
                if self.telemetries[-1].get_state() != 2:
                     self.telemetries[-1].set_state(3) #broken
                     
            ## Append a new telemetry
            tel = Telemetry()
            tel.set_data(_data, n_frame)
            tel.set_payload(t_data)
            tel.set_l_data(l_data)
            tel.set_p_status(p_status)
            
            if t_frame == "0x0400":
                tel.set_state(2) #Finished status
            else:
                tel.set_state(1) #Ongoing status
                
            self.telemetries.append(tel)
            
        elif t_frame == "0x0200": #it is an ending frame
            if len(self.telemetries) != 0: #if this is not true something is wrong
                if self.telemetries[-1].get_state() == 1:
                    self.telemetries[-1].set_state(2) #finished
                else:
                    self.telemetries[-1].set_state(3) #broken
                    
                self.telemetries[-1].set_data(__data, n_frame)
                
        elif t_frame == "0x0300": #it is an ongoing frame
            if len(self.telemetries) != 0: #it this is not true something is wrong
                if self.telemetries[-1].get_state() != 1:
                    self.telemetries[-1].set_state(3) #broken
                    
                self.telemetries[-1].set_data(__data, n_frame)
        
        self.updateTelemetryTable()
        
    def updateTelemetryTable(self):
        
        for i in range(0, len(self.telemetries)):
            self.window.tableWidgetTelemetry.removeRow(i)
            self.window.tableWidgetTelemetry.insertRow(i)
            tel = self.telemetries[i]
            self.window.tableWidgetTelemetry.setItem(i, 0, QtGui.QTableWidgetItem(str(tel.get_state())))
            self.window.tableWidgetTelemetry.setItem(i, 1, QtGui.QTableWidgetItem(str(tel.get_payload())))
            self.window.tableWidgetTelemetry.setItem(i, 2, QtGui.QTableWidgetItem(str(tel.get_l_data())))
            self.window.tableWidgetTelemetry.setItem(i, 3, QtGui.QTableWidgetItem(str(tel.get_lost_p())))
            self.window.tableWidgetTelemetry.setItem(i, 4, QtGui.QTableWidgetItem(str(tel.get_n_data())))
            self.window.tableWidgetTelemetry.setItem(i, 5, QtGui.QTableWidgetItem(str(tel.get_p_status())))
            self.window.tableWidgetTelemetry.setItem(i, 6, QtGui.QTableWidgetItem(','.join(tel.get_data())))
            self.window.tableWidgetTelemetry.show()

    def tc_save(self):
        for i in range(0, len(self.telemetries)):
            self.telemetries[i].save(self.mongo_client)

#    def write_telemtry(self, tex):
#        """
#        Add new telemetry to list
#        """
#        self.winldow.listWidgetTelemetry.addItem(tex)
                    
    def command_clicked(self, item):
        """
        Mueve un comando de la lista, a la salida de texto
        """
        self.window.lineEditSend.setText(item.text())
        
    def open_connection(self):
        """
        Opens connection with server indicated in GUI
        """
        self.window.pushButtonClosePort.setEnabled(True)
        self.window.pushButtonOpenPort.setEnabled(False)
        self.window.pushButtonSend.setEnabled(True)

        url_server = self.window.lineEditURL.text()
        recv_port = self.window.comboBoxPortRecv.currentText()
        send_port = self.window.comboBoxPortSend.currentText()

        try:
            self.client = Client(url_server, send_port, recv_port)
            self.client.new_message.connect(self.write_terminal)
            self.alive = True
        except Exception as e:
            QMessageBox.critical(self, 'Error', 'Error al abrir el puerto serial\n'+str(e))
            self.close_connection()
        
    def close_connection(self):
        """
        Close connections
        """
        self.window.pushButtonClosePort.setEnabled(False)
        self.window.pushButtonOpenPort.setEnabled(True)
        self.window.pushButtonSend.setEnabled(False)
        self.alive = False
        self.client = None

    def send_msg(self):
        """
        Send written message to server
        """
        msg = str(self.window.lineEditSend.text())
        self.addHistory(msg)
        
        #Agregar LF y/o CR
        if self.window.checkBoxLF.isChecked():
            msg += '\n'
        if self.window.checkBoxCR.isChecked():
            msg += '\r'
        
        self.client.send(msg)
        self.window.lineEditSend.clear()
        self.history_cnt = 0
        
    def save_log(self):
        """
        Saves terminal content to file
        """
        doc_file = QFileDialog.getSaveFileName(self, "Guardar archivo", QDir.currentPath(), "Archivos de texto (*.txt);;All files (*.*)")
        doc_file = str(doc_file)
        document = self.window.textEditTerminal.document()
        m_write = QTextDocumentWriter()
        m_write.setFileName(doc_file)
        m_write.setFormat("txt")
        m_write.write(document)
            
    def addHistory(self, line):
        """
        Agrega una nueva linea al historial. Elimina entradas antiguas si supera
        cierta cantidad de mensajes.
        """
        if len(self.history) > 100:
            self.history.pop()
        
        try:
            if not (line == self.history[-1]):
                self.history.append(line)
        except:
            self.history.append(line)
            
    def getHistory(self, index):
        """
        Retorna el elemendo numero index del historial
        """
        if index > 0 and index <= len(self.history):
            return self.history[-index]
        else:
            return ''
    
    def historySend(self):
        """
        Agrega una linea del historial para ser enviada
        """
        if self.history_cnt >= 0:
            if self.history_cnt > len(self.history):
                self.history_cnt = len(self.history)
                
            text = self.getHistory(self.history_cnt)
            self.window.lineEditSend.setText(text)
        else:
            self.history_cnt = 0                
            
    def closeEvent(self, event):
        """
        Cierra la aplicacion correctamente. Cerrar los puertos, detener thread
        y guardar la lista de comandos creada
        """
        if self.alive:
            self.close_connection()
        # file_cmd = open(self.commands_file, 'w')
        # for line in self.commands_list:
        #     file_cmd.write(line+'\n')
        # file_cmd.close()
        event.accept()
        
    def keyPressEvent(self, event):
        """
        Maneja eventos asociados a teclas presionadas
        """
        if event.key() == QtCore.Qt.Key_Up:
            if self.window.lineEditSend.hasFocus():
                self.history_cnt+=1
                self.historySend()
        
        if event.key() == QtCore.Qt.Key_Down:
            if self.window.lineEditSend.hasFocus():
                self.history_cnt-=1
                self.historySend()
                
        #event for temelemetry simulation
        if event.key() == QtCore.Qt.Key_T:
           self.tc_test()
           
        event.accept()
        
    ##########################################
    ###########TEST FUNCTION##################          
    def tc_test(self):
        file = open(sys.argv[1]) 
        for line in file:
            if re.match(r'(.*)Prueba(.*?).*', line):
                print("Start Test")
                continue
    
            elif re.match(r'(.*)exe_cmd(.*?).*', line):
                print(line)
        
            elif len(line) != 0:
                data = line.split(',')
                if len(data) > 1:
                    data[0] = data[0][5:]
                    if data[-1] == '\n':
                        del data[-1]
                    self._process_tm(','.join(data))
        
        file.close()
    ##########################################    
        
            
class EditCommandDialog(QtGui.QDialog):
    """
    Herramienta para edicion de comandos
    """
    def __init__(self,parent=None,cmd_list=[]):
        
        QtGui.QDialog.__init__(self,parent)
        
        self.ventana = Ui_DialogEditCommandList()
        self.ventana.setupUi(self)
        
        self.cmd_list = cmd_list
        self.ventana.listWidgetCommand.addItems(self.cmd_list)
        
        #Conexiones
        self.ventana.pushButtonDelete.clicked.connect(self.delete_item)
        self.ventana.pushButtonAdd.clicked.connect(self.add_item)
        
    def run_tool(self):
        """
        Abre el dialogo para que el usuario la lista. Al cerrar, recupera
        los cambios y retorna la nueva lista
        """
        ret = super(EditCommandDialog, self).exec_()
        9
        if(ret):
            self.cmd_list = []
            for row in range(self.ventana.listWidgetCommand.count()):
                item = self.ventana.listWidgetCommand.item(row)
                self.cmd_list.append(item.text())
         
        return self.cmd_list
    
    def delete_item(self):
        """
        Borra los item seleccionados
        """
        for item in self.ventana.listWidgetCommand.selectedItems():
            row = self.ventana.listWidgetCommand.row(item)
            witem = item = self.ventana.listWidgetCommand.takeItem(row)
            del witem
            
        
    def add_item(self):
        """
        Agrega un nuevo item 
        """
        cmd = self.ventana.lineEditAdd.text()
        item = self.ventana.listWidgetCommand.addItem(cmd)
        


if __name__ == '__main__':
    app = QtGui.QApplication(sys.argv)
    gui = SerialCommander()
    gui.show()
    sys.exit(app.exec_())

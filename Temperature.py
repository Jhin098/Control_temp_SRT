
# Temperature Dashboard - Industrial UI Version (full)

import sys
import csv
import os
import time
import re
import subprocess
from datetime import datetime

import serial
from PyQt5 import QtWidgets, QtCore, QtGui
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import serial.tools.list_ports
from serial_port_finder import find_serial_port

ADMIN_USER = "admin"
ADMIN_PASS = "1234"


# ======================================================
#  Serial Reader Thread
# ======================================================
class SerialReader(QtCore.QThread):
    new_temp = QtCore.pyqtSignal(float)

    def __init__(self, port="COM6", baud=115200, log_writer=None):
        super().__init__()
        self.port = port
        self.baud = baud
        self.running = True
        self.ser = None
        self.pending_emergency_state = None  # queued manual EMG requests
        self.pending_buzzer_command = None   # queued buzzer command
        self.pending_emg_sequence = False    # queued emergency sequence ('R')
        self.pending_full_reset = False      # queued full reset ('H')
        self.buzzer_triggered = False        # track if buzzer was triggered for current over-temp
        self.log_writer = log_writer         # reference to log writer for emergency events

    def open_port(self) -> bool:
        if self.ser is not None and self.ser.is_open:
            return True
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            print(f"[Serial] Connected to {self.ser.portstr}")
            return True
        except Exception as e:
            print(f"[Serial Error] {e}")
            self.ser = None
            return False

    def _write_emergency(self, state: int):
        if not self.open_port():
            print("[Serial Emergency] cannot open port")
            return
        try:
            if state == 1:
                self.ser.write(b"r")
                self.ser.flush()
                print("[Serial Emergency] send 'r' (BOTH Relays ON)")
            else:
                self.ser.write(b"h")
                self.ser.flush()
                print("[Serial Emergency] send 'h' (BOTH Relays OFF)")
        except Exception as e:
            print("[Serial Emergency Write Error]", e)

    def _write_buzzer(self, command: str):
        """Send buzzer command: 'b' = ON (10s), 's' = STOP"""
        if not self.open_port():
            print("[Serial Buzzer] cannot open port")
            return
        try:
            self.ser.write(command.encode())
            self.ser.flush()
            print(f"[Serial Buzzer] send '{command}' ({'ON (10s)' if command == 'b' else 'STOP'})")
        except Exception as e:
            print("[Serial Buzzer Write Error]", e)

    def _write_emergency_sequence(self):
        """Send 'R' = Emergency sequence (Arduino: buzzer 10s -> relay ON latched)"""
        if not self.open_port():
            print("[Serial EMG SEQ] cannot open port")
            return
        try:
            self.ser.write(b"R")
            self.ser.flush()
            print("[Serial EMG SEQ] send 'R' (Buzzer 10s -> Relay ON latched)")
        except Exception as e:
            print("[Serial EMG SEQ Write Error]", e)

    def _write_full_reset(self):
        """Send 'H' = Full reset (Arduino: relay OFF + buzzer OFF + cancel sequence)"""
        if not self.open_port():
            print("[Serial FULL RESET] cannot open port")
            return
        try:
            self.ser.write(b"H")
            self.ser.flush()
            print("[Serial FULL RESET] send 'H' (Relay OFF + Buzzer OFF)")
        except Exception as e:
            print("[Serial FULL RESET Write Error]", e)

    def run(self):
        while self.running:
            if not self.open_port():
                time.sleep(1)
                continue

            try:
                # handle queued emergency sequence command ('R')
                if self.pending_emg_sequence:
                    self.pending_emg_sequence = False
                    self._write_emergency_sequence()

                # handle queued full reset command ('H')
                if self.pending_full_reset:
                    self.pending_full_reset = False
                    self._write_full_reset()

                # handle queued manual EMG command (legacy)
                if self.pending_emergency_state is not None:
                    state = self.pending_emergency_state
                    self.pending_emergency_state = None
                    self._write_emergency(state)

                # handle queued buzzer command
                if self.pending_buzzer_command is not None:
                    cmd = self.pending_buzzer_command
                    self.pending_buzzer_command = None
                    self._write_buzzer(cmd)

                # read serial payload
                if self.ser.in_waiting:
                    line = self.ser.readline().decode(errors="ignore").strip()
                    if not line:
                        time.sleep(0.01)
                        continue

                    # Accept only the agreed protocol from Arduino:
                    #   T=<number>     e.g. T=123.45
                    # and ignore any debug/status lines like READY, RAW=..., T=OPEN, T=SPIKE, etc.
                    if not line.startswith("T="):
                        print(f"[Serial RAW] {line}")
                        continue

                    payload = line[2:].strip()
                    if not payload:
                        continue

                    # Non-numeric payloads mean "invalid reading" -> keep last good value in UI.
                    # Examples: OPEN / WARMUP / SPIKE
                    if not (payload[0].isdigit() or payload[0] in "+-."):
                        continue

                    try:
                        temp = float(payload)
                    except ValueError:
                        continue

                    self.new_temp.emit(temp)

                time.sleep(0.05)

            except Exception as e:
                print(f"[Serial Loop Error] {e}")
                if self.ser is not None:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                time.sleep(1)
                continue

        if self.ser is not None and self.ser.is_open:
            self.ser.close()
            print("[Serial] Closed.")

    def stop(self):
        self.running = False

    @QtCore.pyqtSlot(int)
    def request_emergency(self, state: int):
        self.pending_emergency_state = state

    @QtCore.pyqtSlot(str)
    def request_buzzer(self, command: str):
        """Request buzzer command: 'b' = ON, 's' = STOP"""
        self.pending_buzzer_command = command

    @QtCore.pyqtSlot()
    def request_emergency_sequence(self):
        """Request emergency sequence: buzzer 10s -> relay ON latched (Arduino handles timing)"""
        self.pending_emg_sequence = True

    @QtCore.pyqtSlot()
    def request_full_reset(self):
        """Request full reset: relay OFF + buzzer OFF + cancel sequence"""
        self.pending_full_reset = True

# ======================================================
#  Login Dialog (Admin only)
# ======================================================
class LoginDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Admin Login")
        self.setModal(True)
        self.username = None

        layout = QtWidgets.QVBoxLayout(self)

        lbl_info = QtWidgets.QLabel("Admin only\nEnter username / password")
        layout.addWidget(lbl_info)

        form = QtWidgets.QFormLayout()
        self.edit_user = QtWidgets.QLineEdit()
        self.edit_pass = QtWidgets.QLineEdit()
        self.edit_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("Username:", self.edit_user)
        form.addRow("Password:", self.edit_pass)
        layout.addLayout(form)

        self.lbl_error = QtWidgets.QLabel("")
        self.lbl_error.setStyleSheet("color: red;")
        layout.addWidget(self.lbl_error)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.check_login)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def check_login(self):
        u = self.edit_user.text().strip()
        p = self.edit_pass.text()

        if u == ADMIN_USER and p == ADMIN_PASS:
            self.username = u
            self.accept()
        else:
            self.lbl_error.setText("Invalid username or password")


# ======================================================
#  Over Temp Config Dialog
# ======================================================
class OverTempDialog(QtWidgets.QDialog):
    config_changed = QtCore.pyqtSignal(float)  # new_threshold

    def __init__(self, current_threshold: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Config - Over Temperature")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)

        lbl_th = QtWidgets.QLabel("Over Temp Threshold (°C):")
        layout.addWidget(lbl_th)

        self.spin_th = QtWidgets.QDoubleSpinBox()
        self.spin_th.setRange(-50.0, 800.0)
        self.spin_th.setDecimals(1)
        self.spin_th.setSingleStep(5.0)
        self.spin_th.setValue(current_threshold)
        layout.addWidget(self.spin_th)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def apply(self):
        th = self.spin_th.value()
        self.config_changed.emit(th)
        self.accept()


# ======================================================
#  Offset Config Dialog
# ======================================================
class OffsetDialog(QtWidgets.QDialog):
    config_changed = QtCore.pyqtSignal(float)  # new_offset

    def __init__(self, current_offset: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Config - Offset")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)

        lbl_off = QtWidgets.QLabel("Offset (°C):  (Measured + Offset = Real)")
        layout.addWidget(lbl_off)

        self.spin_off = QtWidgets.QDoubleSpinBox()
        self.spin_off.setRange(-50.0, 50.0)
        self.spin_off.setDecimals(2)
        self.spin_off.setSingleStep(0.1)
        self.spin_off.setValue(current_offset)
        layout.addWidget(self.spin_off)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def apply(self):
        off = self.spin_off.value()
        self.config_changed.emit(off)
        self.accept()


# ======================================================
#  Log Path Config Dialog
# ======================================================
class LogPathDialog(QtWidgets.QDialog):
    config_changed = QtCore.pyqtSignal(str)  # new_path

    def __init__(self, current_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Config - Log Path")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)

        lbl = QtWidgets.QLabel("Select folder to store log files:")
        layout.addWidget(lbl)

        path_layout = QtWidgets.QHBoxLayout()
        self.edit_path = QtWidgets.QLineEdit(current_path)
        btn_browse = QtWidgets.QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse)
        path_layout.addWidget(self.edit_path)
        path_layout.addWidget(btn_browse)
        layout.addLayout(path_layout)

        self.lbl_error = QtWidgets.QLabel("")
        self.lbl_error.setStyleSheet("color: red;")
        layout.addWidget(self.lbl_error)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Log Folder",
            self.edit_path.text() or "C:/"
        )
        if folder:
            self.edit_path.setText(folder)

    def apply(self):
        path = self.edit_path.text().strip()
        if not path:
            self.lbl_error.setText("Path cannot be empty.")
            return
        self.config_changed.emit(path)
        self.accept()

# ======================================================
#  MAIN UI WINDOW (DASHBOARD)
# ======================================================
class MainWindow(QtWidgets.QMainWindow):
    # 0 = normal, 1 = over-temp emergency
    emergency_changed = QtCore.pyqtSignal(int)
    buzzer_command = QtCore.pyqtSignal(str)  # 'b' = ON, 's' = STOP
    emg_sequence_signal = QtCore.pyqtSignal()   # 'R' = emergency sequence
    full_reset_signal = QtCore.pyqtSignal()     # 'H' = full reset

    PAGE_WIDTH_SEC = 700.0

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Temperature Dashboard - Industrial")
        self.resize(1000, 620)

        # config
        self.over_temp_threshold = 420.0
        self.temp_offset = 0.0
        self.log_dir = "C:/Temp_Log"

        self.emergency_state = 0
        self.over_temp_active = False
        self.last_admin_user = None
        self.emg_manual_lock = False
        self.buzzer_triggered = False  # Track if buzzer was triggered for current over-temp event
        self.relay_delay_active = False  # Track if delayed relay activation is in progress
        self.manual_relay_active = False  # Track if manual emergency relay is active
        self.manual_relay_timer = None  # QTimer for manual relay activation
        self.countdown_timer = None  # QTimer for countdown display
        self.countdown_seconds = 0  # Countdown seconds remaining
        self.runtime_timer = None  # QTimer for runtime display
        self.flash_timer = None  # QTimer for button flashing effect
        self.flash_state = False  # Track flash state (on/off)
        self.status_flash_timer = None  # QTimer for status label flashing
        self.status_flash_state = False  # Track status flash state

        self.log_file = None
        self.csv_writer = None

        from collections import deque
        self.data = deque()
        self.max_window_secs = 7 * 24 * 60 * 60

        self.start_time = time.time()
        self.max_page_index_seen = 0

        self.serial_thread = None

        # log file
        self.init_log_for_today()

        # UI
        self.build_ui()
        self.apply_industrial_theme()

        # serial
        auto_port = find_serial_port()
        if not auto_port:
            QtWidgets.QMessageBox.warning(
                self,
                "Serial Not Found",
                "No serial port detected.\nThe dashboard will open without live data.\n\nPlug in your device and restart to stream temperatures.",
            )
            self.set_no_device_mode()
        else:
            print("[UI] Using serial port:", auto_port)
            self.serial_thread = SerialReader(port=auto_port, baud=115200, log_writer=self.log_file)
            self.serial_thread.new_temp.connect(self.handle_new_temp)
            self.emergency_changed.connect(self.serial_thread.request_emergency)
            self.buzzer_command.connect(self.serial_thread.request_buzzer)
            self.emg_sequence_signal.connect(self.serial_thread.request_emergency_sequence)
            self.full_reset_signal.connect(self.serial_thread.request_full_reset)
            self.serial_thread.start()
            
            # Start runtime display timer (updates xlabel every second)
            self.runtime_timer = QtCore.QTimer()
            self.runtime_timer.timeout.connect(self._update_runtime_display)
            self.runtime_timer.start(1000)  # Update every 1 second

    # --------------------------------------------------
    # Build UI
    # --------------------------------------------------
    def build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # top row
        top_layout = QtWidgets.QHBoxLayout()
        main_layout.addLayout(top_layout, 1)

        # Current Temperature
        temp_box = QtWidgets.QGroupBox("Temperature")
        temp_layout = QtWidgets.QVBoxLayout(temp_box)

        self.lbl_temp = QtWidgets.QLabel("0.0 °C")
        f_temp = QtGui.QFont()
        f_temp.setPointSize(60)  # Reduced from 72 to 60
        f_temp.setWeight(QtGui.QFont.DemiBold)
        self.lbl_temp.setFont(f_temp)
        self.lbl_temp.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_temp.setStyleSheet(
            "color: #1D4ED8; font-size: 40pt; font-weight: 600;"  # Reduced weight to 600
        )
        self.lbl_temp.setMinimumHeight(240)
        self.lbl_temp.setMinimumWidth(400)  # Fixed width to prevent resize
        self.lbl_temp.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        temp_layout.addWidget(self.lbl_temp)

        top_layout.addWidget(temp_box, 2)

        # Status
        status_box = QtWidgets.QGroupBox("Status")
        status_layout = QtWidgets.QVBoxLayout(status_box)

        self.lbl_status = QtWidgets.QLabel("NORMAL")
        f_st = QtGui.QFont()
        f_st.setPointSize(32)  # Reduced from 40 to 32
        f_st.setBold(True)
        self.lbl_status.setFont(f_st)
        self.lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #16A34A; font-weight: bold; font-size: 32pt;"  # Added explicit size
        )
        self.lbl_status.setMinimumWidth(350)  # Fixed width to prevent resize
        self.lbl_status.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        status_layout.addWidget(self.lbl_status)

        self.lbl_threshold = QtWidgets.QLabel(
            f"Over Temp Threshold: {self.over_temp_threshold:.1f} °C"
        )
        self.lbl_threshold.setAlignment(QtCore.Qt.AlignCenter)
        status_layout.addWidget(self.lbl_threshold)

        self.lbl_offset = QtWidgets.QLabel(
            f"Offset: {self.temp_offset:+.2f} °C"
        )
        self.lbl_offset.setAlignment(QtCore.Qt.AlignCenter)
        status_layout.addWidget(self.lbl_offset)

        self.lbl_log_path = QtWidgets.QLabel(f"Log Path: {self.log_dir}")
        self.lbl_log_path.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_log_path.setStyleSheet("font-size: 9pt; color: #6B7280;")
        status_layout.addWidget(self.lbl_log_path)

        top_layout.addWidget(status_box, 2)

        # Control panel
        right_box = QtWidgets.QGroupBox("Control")
        right_layout = QtWidgets.QVBoxLayout(right_box)

        self.btn_cfg_over = QtWidgets.QPushButton("Config Over Temp")
        self.btn_cfg_offset = QtWidgets.QPushButton("Config Offset")
        self.btn_cfg_logpath = QtWidgets.QPushButton("Config Log Path")
        self.btn_emg_toggle = QtWidgets.QPushButton("EMG MANUAL: OFF")
        # REMOVED setCheckable(True) to prevent signal loops
        # self.btn_emg_toggle.setCheckable(True) 
        self.btn_reset = QtWidgets.QPushButton("Reset")
        self.btn_open_log = QtWidgets.QPushButton("Open Log Folder")

        for b in (
            self.btn_cfg_over,
            self.btn_cfg_offset,
            self.btn_cfg_logpath,
            self.btn_emg_toggle,
            self.btn_reset,
            self.btn_open_log,
        ):
            b.setMinimumHeight(34)

        right_layout.addWidget(self.btn_cfg_over)
        right_layout.addWidget(self.btn_cfg_offset)
        right_layout.addWidget(self.btn_cfg_logpath)
        right_layout.addSpacing(8)
        right_layout.addWidget(self.btn_emg_toggle)
        right_layout.addWidget(self.btn_reset)
        right_layout.addWidget(self.btn_open_log)
        right_layout.addStretch()

        self.btn_cfg_over.clicked.connect(self.open_overtemp_config)
        self.btn_cfg_offset.clicked.connect(self.open_offset_config)
        self.btn_cfg_logpath.clicked.connect(self.open_logpath_config)
        # CRITICAL FIX: Use clicked instead of toggled to prevent recursive loops
        self.btn_emg_toggle.clicked.connect(self.manual_emergency_toggle)
        self.btn_reset.clicked.connect(self.reset_system)
        self.btn_open_log.clicked.connect(self.open_log_folder)

        top_layout.addWidget(right_box, 1)

        # Graph area
        graph_box = QtWidgets.QGroupBox("Temperature Trend (Real-time, Paged)")
        graph_layout = QtWidgets.QVBoxLayout(graph_box)

        # Page control
        ctrl_layout = QtWidgets.QHBoxLayout()
        lbl_page = QtWidgets.QLabel("Page:")
        ctrl_layout.addWidget(lbl_page)

        self.page_combo = QtWidgets.QComboBox()
        self.page_combo.addItem("Realtime (Current)", -1)
        self.page_combo.currentIndexChanged.connect(self.update_graph)
        ctrl_layout.addWidget(self.page_combo)
        ctrl_layout.addStretch()

        self.lbl_page_range = QtWidgets.QLabel("Page 0 (0–700 s)")
        ctrl_layout.addWidget(self.lbl_page_range)

        graph_layout.addLayout(ctrl_layout)

        # Graph widget (Matplotlib)
        self.fig = Figure(figsize=(6, 3), tight_layout=False)
        self.ax = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.15)

        self.ax.set_title("Temperature Realtime", fontsize=10, fontweight="bold", color="black")
        self.ax.set_xlabel("Seconds: 0", fontsize=10, color="black")  # Will update with runtime
        self.ax.set_ylabel("Temperature (°C)", fontsize=10, color="black")
        self.ax.set_xlim(0, 750)
        self.ax.set_ylim(0, 500)
        self.ax.set_xticks(list(range(0, 751, 100)))
        self.ax.set_yticks(list(range(0, 501, 100)))
        self.ax.grid(True, linestyle="-", alpha=0.5)

        # main line
        (self.curve_line,) = self.ax.plot([], [], color="#2563EB", linewidth=2)
        # threshold line and label
        (self.threshold_line,) = self.ax.plot([0, 750],
                                              [self.over_temp_threshold] * 2,
                                              color="#EF4444",
                                              linewidth=2,
                                              linestyle="--")
        self.th_label = self.ax.text(
            750,
            self.over_temp_threshold,
            self._threshold_label_text(),
            color="#EF4444",
            ha="right",
            va="bottom",
            fontsize=10,
        )

        self.canvas = FigureCanvas(self.fig)
        self.canvas.setMinimumHeight(260)
        graph_layout.addWidget(self.canvas, 1)
        main_layout.addWidget(graph_box, 3)
    def set_no_device_mode(self):
        """Show UI in a safe state when no serial device is connected."""
        self.lbl_status.setText("NO DEVICE")
        self.lbl_status.setStyleSheet(
            "color: #6B7280; font-weight: bold; font-size: 32pt;"
        )
        self.lbl_temp.setText("--.-- °C")
        self.lbl_log_path.setText("Log Path: (no device)")

    # --------------------------------------------------
    def apply_industrial_theme(self):
        self.setStyleSheet(
            """
            QWidget {
                background-color: #E5E7EB;
                font-family: "Segoe UI", "Tahoma";
                font-size: 11pt;
            }
            QGroupBox {
                background-color: #F3F4F6;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 16px;
            }
            QGroupBox:title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: #4B5563;
                font-weight: bold;
            }
            QLabel {
                color: #111827;
            }
            QPushButton {
                background-color: #D1D5DB;
                border-radius: 6px;
                padding: 6px 10px;
                border: 1px solid #9CA3AF;
            }
            QPushButton:hover {
                background-color: #E5E7EB;
            }
            QPushButton:pressed {
                background-color: #9CA3AF;
            }
        """
        )

    # ======================================================
    # Logs
    # ======================================================
    def _ensure_log_dir(self):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception as e:
            print("[LOG PATH ERROR]", e)

    def init_log_for_today(self):
        self._ensure_log_dir()
        date_str = datetime.now().strftime("%Y-%m-%d")
        self.log_filename = os.path.join(self.log_dir, f"temp_log_{date_str}.csv")
        file_exists = os.path.exists(self.log_filename)
        self.log_file = open(self.log_filename, "a", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.log_file)
        if not file_exists:
            self.csv_writer.writerow(
                [
                    "timestamp",
                    "event_type",  # ALERT / CONFIG
                    "user",
                    "temp_raw_c",
                    "temp_adjusted_c",
                    "threshold_c",
                    "old_threshold",
                    "new_threshold",
                    "old_offset",
                    "new_offset",
                ]
            )

    def log_overtemp_event(self, temp_raw: float, temp_adj: float):
        if not self.csv_writer:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.csv_writer.writerow(
            [
                ts,
                "ALERT",
                "",
                f"{temp_raw:.3f}",
                f"{temp_adj:.3f}",
                f"{self.over_temp_threshold:.3f}",
                "",
                "",
                "",
                "",
            ]
        )
        self.log_file.flush()

    def log_config_change(
        self, user: str, old_th: float, new_th: float, old_off: float, new_off: float
    ):
        if not self.csv_writer:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.csv_writer.writerow(
            [
                ts,
                "CONFIG",
                user,
                "",
                "",
                "",
                f"{old_th:.3f}",
                f"{new_th:.3f}",
                f"{old_off:.3f}",
                f"{new_off:.3f}",
            ]
        )
        self.log_file.flush()
    # ======================================================
    #  Handle new temperature from serial (auto EMG)
    # ======================================================
    @QtCore.pyqtSlot(float)
    def handle_new_temp(self, temp_raw: float):
        now_ts = time.time()
        t_sec = now_ts - self.start_time
        temp_adj = temp_raw + self.temp_offset

        self.lbl_temp.setText(f"{temp_adj:.1f} °C")

        if self.emg_manual_lock:
            self._append_graph_data(t_sec, temp_adj)
            self.update_graph()
            return

        # AUTO EMERGENCY
        if temp_adj >= self.over_temp_threshold:
            self.lbl_status.setText("OVER TEMPERATURE")
            self.lbl_status.setStyleSheet(
                "color: #DC2626; font-weight: bold; font-size: 20pt;"
            )

            if not self.over_temp_active:
                self.over_temp_active = True
                self.log_overtemp_event(temp_raw, temp_adj)
                
                # Start status flashing effect (orange warning)
                if not self.status_flash_timer or not self.status_flash_timer.isActive():
                    self.status_flash_state = False
                    self.status_flash_timer = QtCore.QTimer()
                    self.status_flash_timer.timeout.connect(self._flash_status_label)
                    self.status_flash_timer.start(250)  # Flash every 0.25 seconds
                
                # Trigger emergency sequence (only once per over-temp event)
                if not self.buzzer_triggered:
                    self.buzzer_triggered = True
                    self.emergency_state = 1
                    self.manual_relay_active = False  # Will be True after 10s (Arduino handles relay)
                    
                    # Log emergency event
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    log_msg = f"[{timestamp}] AUTO EMERGENCY - Temp: {temp_adj:.1f}°C (Threshold: {self.over_temp_threshold:.1f}°C) - Buzzer 10s then Relay ON\n"
                    
                    if self.log_file:
                        try:
                            self.log_file.write(log_msg)
                            self.log_file.flush()
                        except Exception as e:
                            print(f"[Log Write Error] {e}")
                    
                    print(log_msg.strip())
                    
                    # Send 'R' to Arduino: emergency sequence (buzzer 10s -> relay ON latched)
                    self.emg_sequence_signal.emit()
                    
                    # UI countdown timer (10s) for display only - Arduino handles actual timing
                    self.relay_delay_active = True
                    self.countdown_seconds = 10
                    self.auto_countdown_timer = QtCore.QTimer()
                    self.auto_countdown_timer.timeout.connect(self._auto_countdown_tick)
                    self.auto_countdown_timer.start(1000)

        else:
            # Temperature back to normal
            if self.relay_delay_active and self.buzzer_triggered and not self.manual_relay_active:
                # Still in 10s countdown AND temp dropped below threshold -> AUTO CANCEL
                print(f"[Auto Cancel] Temp {temp_adj:.1f}°C < Threshold {self.over_temp_threshold:.1f}°C -> Cancel emergency")
                
                # Send reset to Arduino (cancel buzzer + relay)
                self.full_reset_signal.emit()
                
                # Stop countdown timer
                if hasattr(self, 'auto_countdown_timer') and self.auto_countdown_timer and self.auto_countdown_timer.isActive():
                    self.auto_countdown_timer.stop()
                    self.auto_countdown_timer = None
                
                # Stop flashing
                if self.status_flash_timer and self.status_flash_timer.isActive():
                    self.status_flash_timer.stop()
                    self.status_flash_timer = None
                
                # Reset state so it can re-trigger if temp goes up again
                self.buzzer_triggered = False
                self.emergency_state = 0
                self.relay_delay_active = False
                self.over_temp_active = False
                
                self.lbl_status.setText("NORMAL")
                self.lbl_status.setStyleSheet(
                    "color: #16A34A; font-weight: bold; font-size: 32pt;"
                )
            elif not self.buzzer_triggered:
                # No emergency was triggered, show NORMAL
                self.lbl_status.setText("NORMAL")
                self.lbl_status.setStyleSheet(
                    "color: #16A34A; font-weight: bold; font-size: 32pt;"
                )
                self.over_temp_active = False
            else:
                # Relay already latched -> only Reset button can turn off
                self.over_temp_active = False

        # append graph data
        self._append_graph_data(t_sec, temp_adj)
        self.update_graph()

    def _auto_countdown_tick(self):
        """UI countdown for auto emergency display only (Arduino handles actual timing)"""
        self.countdown_seconds -= 1
        if self.countdown_seconds > 0:
            self.lbl_status.setText(f"EMERGENCY: {self.countdown_seconds}s")
        else:
            # Countdown finished - Arduino has activated relay by now
            if hasattr(self, 'auto_countdown_timer') and self.auto_countdown_timer:
                self.auto_countdown_timer.stop()
                self.auto_countdown_timer = None
            self.relay_delay_active = False
            self.manual_relay_active = True  # Relay is now definitively ON
            self.lbl_status.setText("!!RELAY ACTIVE!!")
            self.lbl_status.setStyleSheet(
                "color: #B91C1C; font-weight: bold; font-size: 20pt;"
            )
            # Stop status flashing
            if self.status_flash_timer and self.status_flash_timer.isActive():
                self.status_flash_timer.stop()
                self.status_flash_timer = None
            print("[Auto Emergency] Relay activated by Arduino after 10-second buzzer")

    # ======================================================
    #  Append data to graph buffer
    # ======================================================
    def _append_graph_data(self, t_sec: float, temp: float):
        self.data.append((t_sec, temp))

        cutoff = t_sec - self.max_window_secs
        while self.data and self.data[0][0] < cutoff:
            self.data.popleft()

        page_idx = int(t_sec // self.PAGE_WIDTH_SEC)
        if page_idx > self.max_page_index_seen:
            self.max_page_index_seen = page_idx
            self._refresh_page_combo()

    def _refresh_page_combo(self):
        current_page = self.current_page_index()

        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.addItem("Realtime (Current)", -1)
        for i in range(self.max_page_index_seen + 1):
            start = int(i * self.PAGE_WIDTH_SEC)
            end = int((i + 1) * self.PAGE_WIDTH_SEC)
            self.page_combo.addItem(f"Page {i} ({start}–{end} s)", i)
        if current_page == -1:
            self.page_combo.setCurrentIndex(0)
        else:
            self.page_combo.setCurrentIndex(current_page + 1)
        self.page_combo.blockSignals(False)

    def current_page_index(self) -> int:
        idx = self.page_combo.currentIndex()
        if idx <= 0:
            return -1
        data = self.page_combo.itemData(idx, QtCore.Qt.UserRole)
        return int(data) if data is not None else int(idx - 1)

    # ======================================================
    #  Update Graph Display
    # ======================================================
    def update_graph(self):
        if not self.data:
            return

        last_t = self.data[-1][0]

        page_idx = self.current_page_index()
        if page_idx == -1:
            page_idx = int(last_t // self.PAGE_WIDTH_SEC)

        start = page_idx * self.PAGE_WIDTH_SEC
        end = (page_idx + 1) * self.PAGE_WIDTH_SEC

        subset = [(t, v) for (t, v) in self.data if start <= t <= end]
        if not subset:
            self.curve_line.set_data([], [])
        else:
            xs, ys = zip(*subset)
            xs_rel = [x - start for x in xs]
            self.curve_line.set_data(xs_rel, ys)

        # fixed axes
        self.ax.set_xlim(0, 750)
        self.ax.set_ylim(0, 500)

        self.lbl_page_range.setText(f"Page {page_idx} ({int(start)}–{int(end)} s)")

        self.ax.set_xticks(list(range(0, 751, 100)))
        self.ax.set_yticks(list(range(0, 501, 100)))
        
        # Update xlabel with runtime
        runtime = int(time.time() - self.start_time)
        self.ax.set_xlabel(f"Seconds: {runtime}", fontsize=10, color="black")

        # threshold
        self.threshold_line.set_data([0, 750],
                                     [self.over_temp_threshold] * 2)
        self.th_label.set_text(self._threshold_label_text())
        self.th_label.set_position((750, self.over_temp_threshold))

        self.canvas.draw_idle()

    def _threshold_label_text(self) -> str:
        return f"TH {self.over_temp_threshold:.1f} °C"
    # ======================================================
    # Manual EMG + Reset System
    # ======================================================
    # ======================================================
    # Manual EMG + Reset System
    # ======================================================
    def manual_emergency_toggle(self):
        """EMG MANUAL button handler - ON ONLY. OFF is via Reset button."""
        # If already active or in progress, do nothing
        if self.emg_manual_lock:
            return

        # EMG MANUAL ON
        self.emg_manual_lock = True
        self.btn_emg_toggle.setEnabled(False)

        # Log emergency event
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] EMERGENCY MANUAL ACTIVATED - Buzzer 10s then Relay ON (Arduino sequence)\n"
        if self.log_file:
            try:
                self.log_file.write(log_msg)
                self.log_file.flush()
            except Exception as e:
                print(f"[Log Write Error] {e}")

        print(log_msg.strip())

        # Send 'R' to Arduino: emergency sequence (buzzer 10s -> relay ON latched)
        self.emg_sequence_signal.emit()

        # Set emergency state
        self.emergency_state = 1
        self.manual_relay_active = False  # Will be True after 10s (Arduino handles relay)

        # UI countdown timer (Arduino handles actual buzzer/relay timing)
        self.countdown_seconds = 10
        self.countdown_timer = QtCore.QTimer()
        self.countdown_timer.timeout.connect(self._update_countdown)
        self.countdown_timer.start(1000)

        # Schedule UI update after 10 seconds (relay is now ON per Arduino)
        self.manual_relay_timer = QtCore.QTimer()
        self.manual_relay_timer.setSingleShot(True)
        self.manual_relay_timer.timeout.connect(self._activate_manual_emergency_relay)
        self.manual_relay_timer.start(10000)

        # Start flashing effect
        self.flash_state = False
        self.flash_timer = QtCore.QTimer()
        self.flash_timer.timeout.connect(self._flash_button)
        self.flash_timer.start(500)

        self.lbl_status.setText("!!EMERGENCY!!")
        self.lbl_status.setStyleSheet(
            "color: #B91C1C; font-weight: bold; font-size: 32pt;"
        )
        self.btn_emg_toggle.setText(f"EMG MANUAL: {self.countdown_seconds}s")

        # Highlight Reset button in blue so user knows where to stop
        self.btn_reset.setStyleSheet(
            "background-color: #2563EB; color: white; font-weight: bold; font-size: 10pt; border: 2px solid #1D4ED8; border-radius: 4px;"
        )
        self.btn_reset.setText("⏹ Reset ")
    
    def _update_runtime_display(self):
        """Update xlabel with total runtime in seconds"""
        runtime = int(time.time() - self.start_time)
        self.ax.set_xlabel(f"Seconds: {runtime}", fontsize=10, color="black")
        self.canvas.draw_idle()
    
    def _flash_button(self):
        """Flash EMG MANUAL button between red colors for danger warning"""
        self.flash_state = not self.flash_state
        if self.flash_state:
            # Bright red
            self.btn_emg_toggle.setStyleSheet(
                "background-color: #DC2626; color: white; font-weight: bold;"
            )
        else:
            # Dark red
            self.btn_emg_toggle.setStyleSheet(
                "background-color: #991B1B; color: white; font-weight: bold;"
            )
    
    def _flash_status_label(self):
        """Flash status label between orange colors for over temperature warning"""
        self.status_flash_state = not self.status_flash_state
        if self.status_flash_state:
            # Bright orange
            self.lbl_status.setStyleSheet(
                "color: #EA580C; font-weight: bold; font-size: 20pt;"
            )
        else:
            # Dark orange
            self.lbl_status.setStyleSheet(
                "color: #C2410C; font-weight: bold; font-size: 20pt;"
            )
    
    def _update_countdown(self):
        """Update countdown display on EMG MANUAL button"""
        self.countdown_seconds -= 1
        if self.countdown_seconds > 0:
            # Block signals to prevent re-triggering toggled event
            self.btn_emg_toggle.blockSignals(True)
            self.btn_emg_toggle.setText(f"EMG MANUAL: {self.countdown_seconds}s")
            self.btn_emg_toggle.blockSignals(False)
        else:
            # Countdown finished, stop timer only (don't change text, let relay logic handle it)
            if self.countdown_timer:
                self.countdown_timer.stop()
                self.countdown_timer = None
            # Do NOT set text here, let _activate_manual_emergency_relay handle it to avoid race condition
    
    def _activate_manual_emergency_relay(self):
        """UI update after 10-second countdown. Arduino has already activated relay."""
        if self.emergency_state == 1:
            self.manual_relay_active = True  # Arduino relay is now ON

            # Stop countdown timer
            if self.countdown_timer and self.countdown_timer.isActive():
                self.countdown_timer.stop()
                self.countdown_timer = None

            # Stop flash timer
            if self.flash_timer and self.flash_timer.isActive():
                self.flash_timer.stop()
                self.flash_timer = None

            # Solid red background and text
            self.btn_emg_toggle.setStyleSheet(
                "background-color: #DC2626; color: white; font-weight: bold;"
            )
            self.btn_emg_toggle.setText("EMG MANUAL: ON")
            self.lbl_status.setText("!!RELAY ACTIVE!!")
            self.lbl_status.setStyleSheet(
                "color: #B91C1C; font-weight: bold; font-size: 32pt;"
            )
            print("[Relay] Manual emergency relay activated by Arduino - LATCHED ON")

    def reset_system(self):
        """Reset emergency system and turn OFF relay and buzzer. ONLY way to turn OFF relay."""
        self.emg_manual_lock = False
        self.manual_relay_active = False

        if self.emergency_state != 0:
            self.emergency_state = 0
            # Send 'H' to Arduino: full reset (relay OFF + buzzer OFF + cancel sequence)
            self.full_reset_signal.emit()

        # Stop all timers
        if self.manual_relay_timer and self.manual_relay_timer.isActive():
            self.manual_relay_timer.stop()
            self.manual_relay_timer = None
        if self.countdown_timer and self.countdown_timer.isActive():
            self.countdown_timer.stop()
            self.countdown_timer = None
        if self.flash_timer and self.flash_timer.isActive():
            self.flash_timer.stop()
            self.flash_timer = None
        if self.status_flash_timer and self.status_flash_timer.isActive():
            self.status_flash_timer.stop()
            self.status_flash_timer = None
        # Stop auto countdown timer if active
        if hasattr(self, 'auto_countdown_timer') and self.auto_countdown_timer and self.auto_countdown_timer.isActive():
            self.auto_countdown_timer.stop()
            self.auto_countdown_timer = None

        # Re-enable and reset EMG button
        self.btn_emg_toggle.setEnabled(True)
        self.btn_emg_toggle.setText("EMG MANUAL: OFF")
        self.btn_emg_toggle.setStyleSheet("")

        # Reset button back to normal style
        self.btn_reset.setStyleSheet("")
        self.btn_reset.setText("Reset")

        self.over_temp_active = False
        self.buzzer_triggered = False
        self.relay_delay_active = False
        
        self.lbl_status.setText("NORMAL")
        self.lbl_status.setStyleSheet(
            "color: #16A34A; font-weight: bold; font-size: 32pt;"
        )

        self.data.clear()
        self.lbl_temp.setText("0.0 °C")
        self.curve_line.set_data([], [])
        self.threshold_line.set_data([], [])
        self.th_label.set_text(self._threshold_label_text())
        self.start_time = time.time()
        self.max_page_index_seen = 0
        self.page_combo.clear()
        self.page_combo.addItem("Realtime (Current)", -1)
        self.lbl_page_range.setText("Page 0 (0–700 s)")

    # ======================================================
    # Popup (center modal)
    # ======================================================
    def show_overtemp_popup(self, temp_adj: float):
        popup = QtWidgets.QMessageBox(self)
        popup.setIcon(QtWidgets.QMessageBox.Warning)
        popup.setWindowTitle("OVER TEMPERATURE ALERT")
        popup.setText(
            f"<div style='font-size:22pt; color:#DC2626; font-weight:bold;'>"
            f"OVER TEMPERATURE!"
            f"</div>"
            f"<div style='font-size:14pt; margin-top:10px;'>"
            f"Current Temperature: <b>{temp_adj:.2f} °C</b><br>"
            f"Threshold: <b>{self.over_temp_threshold:.1f} °C</b>"
            f"</div>"
        )
        popup.setStandardButtons(QtWidgets.QMessageBox.Ok)

        popup.setStyleSheet(
            """
            QMessageBox {
                background-color: #FFF9C4;
            }
            QMessageBox QLabel {
                color: #7C2D12;
                font-size: 14pt;
            }
            QMessageBox QPushButton {
                background-color: #FCD34D;
                border: 1px solid #F59E0B;
                padding: 6px 16px;
                font-size: 12pt;
                font-weight: bold;
            }
            QMessageBox QPushButton:hover {
                background-color: #FBBF24;
            }
        """
        )

        popup.exec_()

    # ======================================================
    # Config buttons
    # ======================================================
    def open_overtemp_config(self):
        login = LoginDialog(self)
        if login.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.last_admin_user = login.username or "unknown"

        old_th = self.over_temp_threshold
        old_off = self.temp_offset

        dlg = OverTempDialog(self.over_temp_threshold, self)
        dlg.config_changed.connect(
            lambda new_th: self.apply_config(new_th, old_th, old_off, kind="threshold")
        )
        dlg.exec_()

    def open_offset_config(self):
        login = LoginDialog(self)
        if login.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.last_admin_user = login.username or "unknown"

        old_th = self.over_temp_threshold
        old_off = self.temp_offset

        dlg = OffsetDialog(self.temp_offset, self)
        dlg.config_changed.connect(
            lambda new_off: self.apply_config(new_off, old_th, old_off, kind="offset")
        )
        dlg.exec_()

    def open_logpath_config(self):
        login = LoginDialog(self)
        if login.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.last_admin_user = login.username or "unknown"

        dlg = LogPathDialog(self.log_dir, self)
        dlg.config_changed.connect(self.apply_log_path)
        dlg.exec_()

    def apply_config(self, value: float, old_th: float, old_off: float, kind: str):
        if kind == "threshold":
            new_th = value
            new_off = old_off
        else:
            new_th = old_th
            new_off = value

        self.over_temp_threshold = new_th
        self.temp_offset = new_off

        self.lbl_threshold.setText(
            f"Over Temp Threshold: {self.over_temp_threshold:.1f} °C"
        )
        self.lbl_offset.setText(f"Offset: {self.temp_offset:+.2f} °C")

        user = self.last_admin_user or "unknown"
        self.log_config_change(user, old_th, new_th, old_off, new_off)

        self.update_graph()

    def apply_log_path(self, new_path: str):
        if self.log_file:
            self.log_file.close()
            self.log_file = None
            self.csv_writer = None

        self.log_dir = new_path
        self.lbl_log_path.setText(f"Log Path: {self.log_dir}")
        self.init_log_for_today()

    # ======================================================
    # Log folder + closeEvent
    # ======================================================
    def open_log_folder(self):
        try:
            self._ensure_log_dir()
            path = os.path.abspath(self.log_dir)
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Open Log Folder", f"Cannot open log folder:\n{e}"
            )

    def closeEvent(self, event):
        if self.serial_thread is not None:
            self.serial_thread.stop()
            self.serial_thread.wait()

        if self.log_file:
            self.log_file.close()

        event.accept()


# ======================================================
def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

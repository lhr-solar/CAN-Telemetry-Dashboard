from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS
import threading
import queue
from candapter_reader import CandapterReader
from can_decoder import CANDecoder
from can_device import CANDevice
from init_can_devices import init_can_devices

app = Flask(
    __name__,
    static_folder='../build/client',
    static_url_path='',
)

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

can_decoder = CANDecoder()
can_decoder.find_add_dbc_files()
CANDevice.can_decoder = can_decoder

can_reader = CandapterReader(
    com_port="COM4",
    serial_baudrate=9600,
    can_baudrate=125000
)
can_reader.connect()

can_queue = queue.Queue(maxsize=500)
data_available = threading.Event()

def can_reader_task():
    try:
        while True:
            try:
                if can_reader.candapter_connected:
                    can_queue.put(can_reader.read(), timeout=0)
                    data_available.set()
            except queue.Full:
                print("Queue is full")
                pass
    except KeyboardInterrupt:
        print("\nStopping CAN message reader.")
    finally:
        can_reader.adapter.closeCANBus()
        can_reader.adapter.closeDevice()
        can_reader.candapter_connected = False
        print("CAN bus and device closed.")


def can_processor_task():
    while True:
        data_available.wait()
        try:
            raw_message = can_queue.get_nowait()
            if raw_message is None:
                continue
            dm = CANDevice.process_can_message(raw_message)
            # print(dm)
        except queue.Empty:
            data_available.clear()


emit_thread = None
emit_thread_lock = threading.Lock()


def emit_can_data():
    while True:
        socketio.sleep(0.1)  # 100ms update interval
        with emit_thread_lock:
            if emit_thread is None:
                break

            socketio.emit('can_update', {
                "BATTERY": CANDevice.get_device_by_name("BATTERY").master_data,
                "MPPT_A": CANDevice.get_device_by_name("MPPT_A").master_data,
                "MPPT_B": CANDevice.get_device_by_name("MPPT_B").master_data,
            })
            socketio.emit('connection_state', {
                "CANDAPTER": can_reader.candapter_connected,
                "BATTERY": CANDevice.get_device_by_name("BATTERY").is_connected,
                "MPPT_A": CANDevice.get_device_by_name("MPPT_A").is_connected,
                "MPPT_B": CANDevice.get_device_by_name("MPPT_B").is_connected,
                "MOTOR_CONTROLLER": CANDevice.get_device_by_name("MOTOR_CONTROLLER").is_connected
            })


init_can_devices()

can_reader_thread = threading.Thread(target=can_reader_task)
can_reader_thread.daemon = True

can_processor_thread = threading.Thread(target=can_processor_task)
can_processor_thread.daemon = True


can_reader_thread.start()
can_processor_thread.start()


@app.route('/')
def index():
    return app.send_static_file('index.html')


@socketio.on('connect')
def handle_connect():
    global emit_thread
    print('Client connected')

    with emit_thread_lock:
        if emit_thread is None:
            emit_thread = socketio.start_background_task(emit_can_data)


@socketio.on('disconnect')
def handle_disconnect():
    global emit_thread
    with emit_thread_lock:
        if emit_thread is not None:
            emit_thread = None
    print('Client disconnected')


@socketio.on('bps_reset')
def handle_trip_reset():
    print('BPS RESET command received')
    CANDevice.get_device_by_name("BATTERY").reset()


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

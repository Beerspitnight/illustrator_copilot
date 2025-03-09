from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
socketio = SocketIO(app)

@app.route('/')
def index():
    return "Hello, Illustrator Co-Pilot!"

if __name__ == '__main__':
    socketio.run(app, debug=True)

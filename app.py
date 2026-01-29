# ---------------------------------------------------------
# CRITICAL: These lines must be at the very top
# ---------------------------------------------------------
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import uuid
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key')

# Initialize SocketIO with eventlet
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- DATA STORE ---
active_rooms = {}
level_configs = {}

def generate_levels():
    levels = {}
    for i in range(1, 101):
        if i <= 30:
            difficulty, grid_size, ai_skill, time_limit = 'easy', 3 + (i // 10), 0.4, 180
        elif i <= 70:
            difficulty, grid_size, ai_skill, time_limit = 'medium', 4 + (i // 20), 0.7, 150
        else:
            difficulty, grid_size, ai_skill, time_limit = 'hard', 6 + (i // 30), 0.95, 120
        
        levels[i] = {
            'level': i, 'difficulty': difficulty, 
            'grid_size': min(grid_size, 10), 
            'ai_skill': ai_skill, 'time_limit': time_limit
        }
    return levels

level_configs = generate_levels()

class DotsBoxesGame:
    def __init__(self, grid_size=5, ai_skill=0.5, time_limit=120):
        self.grid_size = grid_size
        self.ai_skill = ai_skill
        self.time_limit = time_limit
        self.start_time = datetime.now()
        self.current_player = 1
        self.scores = [0, 0]
        self.horizontal_lines = [[0] * (grid_size - 1) for _ in range(grid_size)]
        self.vertical_lines = [[0] * grid_size for _ in range(grid_size - 1)]
        self.boxes = [[0] * (grid_size - 1) for _ in range(grid_size - 1)]
        self.game_over = False
        self.winner = None
        self.time_up = False

    def check_time(self):
        if self.time_limit <= 0: return False
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed >= self.time_limit:
            self.time_up = True
            self.game_over = True
            self.determine_winner()
            return True
        return False

    def get_remaining_time(self):
        if self.time_limit <= 0: return 0
        elapsed = (datetime.now() - self.start_time).total_seconds()
        return max(0, int(self.time_limit - elapsed))

    def make_move(self, line_type, i, j, player):
        if self.game_over: return {'success': False, 'message': 'Game Over'}
        if self.check_time(): return {'success': False, 'message': 'Time Up'}

        if line_type == 'horizontal':
            if not (0 <= i < self.grid_size and 0 <= j < self.grid_size - 1): return {'success': False}
            if self.horizontal_lines[i][j] != 0: return {'success': False}
            self.horizontal_lines[i][j] = player
        elif line_type == 'vertical':
            if not (0 <= i < self.grid_size - 1 and 0 <= j < self.grid_size): return {'success': False}
            if self.vertical_lines[i][j] != 0: return {'success': False}
            self.vertical_lines[i][j] = player
        
        box_completed = self.check_boxes(line_type, i, j, player)
        
        if not box_completed:
            self.current_player = 3 - self.current_player
        
        if self.is_full():
            self.game_over = True
            self.determine_winner()
            
        return {'success': True, 'box_completed': box_completed, 'game_over': self.game_over}

    def check_boxes(self, line_type, i, j, player):
        completed = False
        def is_complete(r, c):
            return (self.horizontal_lines[r][c] and self.horizontal_lines[r+1][c] and
                    self.vertical_lines[r][c] and self.vertical_lines[r][c+1])

        if line_type == 'horizontal':
            if i > 0 and is_complete(i-1, j):
                if self.boxes[i-1][j] == 0:
                    self.boxes[i-1][j] = player; self.scores[player-1] += 1; completed = True
            if i < self.grid_size - 1 and is_complete(i, j):
                if self.boxes[i][j] == 0:
                    self.boxes[i][j] = player; self.scores[player-1] += 1; completed = True
        else:
            if j > 0 and is_complete(i, j-1):
                if self.boxes[i][j-1] == 0:
                    self.boxes[i][j-1] = player; self.scores[player-1] += 1; completed = True
            if j < self.grid_size - 1 and is_complete(i, j):
                if self.boxes[i][j] == 0:
                    self.boxes[i][j] = player; self.scores[player-1] += 1; completed = True
        return completed

    def is_full(self):
        for r in range(self.grid_size - 1):
            for c in range(self.grid_size - 1):
                if self.boxes[r][c] == 0: return False
        return True

    def determine_winner(self):
        if self.scores[0] > self.scores[1]: self.winner = 1
        elif self.scores[1] > self.scores[0]: self.winner = 2
        else: self.winner = 0

    def get_state(self):
        return {
            'grid_size': self.grid_size, 'current_player': self.current_player,
            'scores': self.scores, 'horizontal_lines': self.horizontal_lines,
            'vertical_lines': self.vertical_lines, 'boxes': self.boxes,
            'game_over': self.game_over, 'winner': self.winner,
            'time_up': self.time_up, 'remaining_time': self.get_remaining_time()
        }
    
    def get_valid_moves(self):
        moves = []
        for r in range(self.grid_size):
            for c in range(self.grid_size-1):
                if not self.horizontal_lines[r][c]: moves.append(('horizontal', r, c))
        for r in range(self.grid_size-1):
            for c in range(self.grid_size):
                if not self.vertical_lines[r][c]: moves.append(('vertical', r, c))
        return moves
    
    def get_ai_move(self):
        moves = self.get_valid_moves()
        if not moves: return None
        # Simple AI
        return random.choice(moves)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/levels')
def get_levels_route():
    return jsonify(level_configs)

@app.route('/api/start_single', methods=['POST'])
def start_single():
    data = request.json
    lvl = data.get('level', 1)
    cfg = level_configs.get(lvl, level_configs[1])
    game_id = str(uuid.uuid4())
    game = DotsBoxesGame(cfg['grid_size'], cfg['ai_skill'], cfg['time_limit'])
    active_rooms[game_id] = {'game': game, 'mode': 'single', 'created': datetime.now()}
    return jsonify({'game_id': game_id, 'state': game.get_state(), 'config': cfg})

@app.route('/api/move', methods=['POST'])
def move_route():
    data = request.json
    game_id = data.get('game_id')
    if game_id not in active_rooms: return jsonify({'error': 'Game not found'}), 404
    
    room = active_rooms[game_id]
    game = room['game']
    
    # Human Move
    res = game.make_move(data['type'], data['i'], data['j'], 1)
    if not res['success']: return jsonify({'error': 'Invalid move'}), 400
    
    # AI Loop (Single Player)
    if room['mode'] == 'single' and not game.game_over and game.current_player == 2:
        while game.current_player == 2 and not game.game_over:
            # NON-BLOCKING SLEEP prevents freezing
            eventlet.sleep(0.3)
            
            ai_move = game.get_ai_move()
            if not ai_move: break
            ai_res = game.make_move(ai_move[0], ai_move[1], ai_move[2], 2)
            if not ai_res['box_completed']: break
            
    return jsonify({'state': game.get_state()})

@app.route('/api/check_time', methods=['POST'])
def check_time_route():
    game_id = request.json.get('game_id')
    if game_id not in active_rooms: return jsonify({'error': '404'}), 404
    game = active_rooms[game_id]['game']
    game.check_time()
    return jsonify({'state': game.get_state()})

@socketio.on('create_room')
def on_create(data):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    game = DotsBoxesGame(int(data.get('grid_size', 5)), 0, 0)
    active_rooms[code] = {'game': game, 'mode': 'multi', 'players': {}, 'created': datetime.now()}
    join_room(code)
    active_rooms[code]['players'][request.sid] = 1
    emit('room_created', {'code': code, 'player': 1, 'state': game.get_state()})

@socketio.on('join_room')
def on_join(data):
    code = data.get('code')
    if code not in active_rooms: return emit('error', {'msg': 'Room not found'})
    room = active_rooms[code]
    if len(room['players']) >= 2: return emit('error', {'msg': 'Room full'})
    
    join_room(code)
    room['players'][request.sid] = 2
    emit('game_start', {'code': code, 'player': 2, 'state': room['game'].get_state()})
    emit('opponent_joined', room=code)

@socketio.on('multi_move')
def on_multi_move(data):
    code = data.get('code')
    if code not in active_rooms: return
    room = active_rooms[code]
    game = room['game']
    
    player_num = room['players'].get(request.sid)
    if game.current_player != player_num: return
    
    res = game.make_move(data['type'], data['i'], data['j'], player_num)
    if res['success']:
        emit('update_state', {'state': game.get_state()}, room=code)

@socketio.on('disconnect')
def on_disconnect():
    for code, room in active_rooms.items():
        if request.sid in room['players']:
            emit('opponent_left', room=code)

if __name__ == '__main__':
    socketio.run(app, debug=True)

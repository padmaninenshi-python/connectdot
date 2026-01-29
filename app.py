import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import uuid
from datetime import datetime
import os
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key')

# IMPORTANT: cors_allowed_origins="*" is required for external access
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Store active rooms and games
active_rooms = {}
level_configs = {}

# Generate 100 levels
def generate_levels():
    levels = {}
    for i in range(1, 101):
        if i <= 30:
            difficulty = 'easy'
            grid_size = 3 + (i // 10)
            ai_skill = 0.4
            time_limit = 180
        elif i <= 70:
            difficulty = 'medium'
            grid_size = 4 + (i // 20)
            ai_skill = 0.7
            time_limit = 150
        else:
            difficulty = 'hard'
            grid_size = 6 + (i // 30)
            ai_skill = 0.95
            time_limit = 120
        
        levels[i] = {
            'level': i,
            'difficulty': difficulty,
            'grid_size': min(grid_size, 10),
            'ai_skill': ai_skill,
            'time_limit': time_limit
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
    
    def check_time_limit(self):
        if self.time_limit <= 0: return False
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed >= self.time_limit:
            self.time_up = True
            self.game_over = True
            self.determine_winner()
            return True
        return False
    
    def get_remaining_time(self):
        if self.time_limit <= 0: return None
        elapsed = (datetime.now() - self.start_time).total_seconds()
        return int(max(0, self.time_limit - elapsed))
    
    def make_move(self, line_type, i, j, player):
        if self.game_over: return {'success': False, 'message': 'Game is over'}
        if self.check_time_limit(): return {'success': False, 'message': 'Time is up!'}
        
        # Validate move
        if line_type == 'horizontal':
            if i < 0 or i >= self.grid_size or j < 0 or j >= self.grid_size - 1:
                return {'success': False, 'message': 'Invalid position'}
            if self.horizontal_lines[i][j] != 0:
                return {'success': False, 'message': 'Line already taken'}
            self.horizontal_lines[i][j] = player
        else:
            if i < 0 or i >= self.grid_size - 1 or j < 0 or j >= self.grid_size:
                return {'success': False, 'message': 'Invalid position'}
            if self.vertical_lines[i][j] != 0:
                return {'success': False, 'message': 'Line already taken'}
            self.vertical_lines[i][j] = player
        
        box_completed = self.check_boxes(line_type, i, j, player)
        
        if not box_completed:
            self.current_player = 2 if self.current_player == 1 else 1
        
        if self.is_game_over():
            self.game_over = True
            self.determine_winner()
        
        return {
            'success': True,
            'box_completed': box_completed,
            'current_player': self.current_player,
            'scores': self.scores,
            'game_over': self.game_over,
            'winner': self.winner,
            'time_up': self.time_up
        }
    
    def check_boxes(self, line_type, i, j, player):
        completed = False
        if line_type == 'horizontal':
            if i > 0 and self.is_box_complete(i - 1, j):
                self.boxes[i - 1][j] = player
                self.scores[player - 1] += 1
                completed = True
            if i < self.grid_size - 1 and self.is_box_complete(i, j):
                self.boxes[i][j] = player
                self.scores[player - 1] += 1
                completed = True
        else:
            if j > 0 and self.is_box_complete(i, j - 1):
                self.boxes[i][j - 1] = player
                self.scores[player - 1] += 1
                completed = True
            if j < self.grid_size - 1 and self.is_box_complete(i, j):
                self.boxes[i][j] = player
                self.scores[player - 1] += 1
                completed = True
        return completed
    
    def is_box_complete(self, i, j):
        return (self.horizontal_lines[i][j] != 0 and
                self.horizontal_lines[i + 1][j] != 0 and
                self.vertical_lines[i][j] != 0 and
                self.vertical_lines[i][j + 1] != 0 and
                self.boxes[i][j] == 0)
    
    def is_game_over(self):
        for i in range(self.grid_size - 1):
            for j in range(self.grid_size - 1):
                if self.boxes[i][j] == 0: return False
        return True
    
    def determine_winner(self):
        if self.scores[0] > self.scores[1]: self.winner = 1
        elif self.scores[1] > self.scores[0]: self.winner = 2
        else: self.winner = 0
    
    def get_ai_move(self):
        available_moves = self.get_available_moves()
        if not available_moves: return None
        
        completing_moves = [move for move in available_moves if self.move_completes_box(move)]
        if completing_moves: return random.choice(completing_moves)
        
        if random.random() < self.ai_skill:
            safe_moves = [move for move in available_moves if not self.move_gives_box(move)]
            if safe_moves: return random.choice(safe_moves)
        
        return random.choice(available_moves)
    
    def get_available_moves(self):
        moves = []
        for i in range(self.grid_size):
            for j in range(self.grid_size - 1):
                if self.horizontal_lines[i][j] == 0:
                    moves.append(('horizontal', i, j))
        for i in range(self.grid_size - 1):
            for j in range(self.grid_size):
                if self.vertical_lines[i][j] == 0:
                    moves.append(('vertical', i, j))
        return moves
    
    def move_completes_box(self, move):
        line_type, i, j = move
        if line_type == 'horizontal':
            if i > 0 and self.count_box_sides(i - 1, j) == 3: return True
            if i < self.grid_size - 1 and self.count_box_sides(i, j) == 3: return True
        else:
            if j > 0 and self.count_box_sides(i, j - 1) == 3: return True
            if j < self.grid_size - 1 and self.count_box_sides(i, j) == 3: return True
        return False
    
    def move_gives_box(self, move):
        line_type, i, j = move
        if line_type == 'horizontal':
            if i > 0 and self.count_box_sides(i - 1, j) == 2: return True
            if i < self.grid_size - 1 and self.count_box_sides(i, j) == 2: return True
        else:
            if j > 0 and self.count_box_sides(i, j - 1) == 2: return True
            if j < self.grid_size - 1 and self.count_box_sides(i, j) == 2: return True
        return False
    
    def count_box_sides(self, i, j):
        count = 0
        if self.horizontal_lines[i][j] != 0: count += 1
        if self.horizontal_lines[i + 1][j] != 0: count += 1
        if self.vertical_lines[i][j] != 0: count += 1
        if self.vertical_lines[i][j + 1] != 0: count += 1
        return count
    
    def get_state(self):
        return {
            'grid_size': self.grid_size,
            'current_player': self.current_player,
            'scores': self.scores,
            'horizontal_lines': self.horizontal_lines,
            'vertical_lines': self.vertical_lines,
            'boxes': self.boxes,
            'game_over': self.game_over,
            'winner': self.winner,
            'time_up': self.time_up,
            'remaining_time': self.get_remaining_time()
        }

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/room/<room_code>')
def join_room_page(room_code):
    return render_template('index.html', room_code=room_code)

@app.route('/api/levels')
def get_levels():
    return jsonify(level_configs)

@app.route('/api/start_single_player', methods=['POST'])
def start_single_player():
    data = request.json
    level = data.get('level', 1)
    if level not in level_configs: return jsonify({'error': 'Invalid level'}), 400
    
    config = level_configs[level]
    game_id = str(uuid.uuid4())
    
    game = DotsBoxesGame(config['grid_size'], config['ai_skill'], config['time_limit'])
    active_rooms[game_id] = {'game': game, 'mode': 'single_player', 'level': level, 'created_at': datetime.now()}
    
    return jsonify({'game_id': game_id, 'state': game.get_state(), 'level_info': config})

@app.route('/api/make_move', methods=['POST'])
def make_move():
    data = request.json
    game_id = data.get('game_id')
    line_type = data.get('line_type')
    i = data.get('i')
    j = data.get('j')
    
    if game_id not in active_rooms: return jsonify({'error': 'Game not found'}), 404
    game = active_rooms[game_id]['game']
    
    if game.check_time_limit():
        return jsonify({'state': game.get_state(), 'move_result': {'success': False, 'message': 'Time is up!'}})
    
    result = game.make_move(line_type, i, j, 1)
    if not result['success']:
        return jsonify({'error': result['message'], 'state': game.get_state()}), 400
    
    response = {'state': game.get_state(), 'move_result': result}
    
    # AI Turn
    if active_rooms[game_id]['mode'] == 'single_player' and not game.game_over and game.current_player == 2:
        while game.current_player == 2 and not game.game_over:
            if game.check_time_limit():
                response['state'] = game.get_state()
                break
            
            # Using socketio.sleep instead of time.sleep to avoid blocking
            socketio.sleep(0.3 + (game.ai_skill * 0.5))
            
            ai_move = game.get_ai_move()
            if ai_move:
                ai_result = game.make_move(ai_move[0], ai_move[1], ai_move[2], 2)
                response['ai_move'] = {'line_type': ai_move[0], 'i': ai_move[1], 'j': ai_move[2], 'result': ai_result}
                response['state'] = game.get_state()
                if not ai_result.get('box_completed'): break
            else:
                break
    
    return jsonify(response)

@app.route('/api/check_time', methods=['POST'])
def check_time():
    data = request.json
    game_id = data.get('game_id')
    if game_id not in active_rooms: return jsonify({'error': 'Game not found'}), 404
    game = active_rooms[game_id]['game']
    game.check_time_limit()
    return jsonify({'remaining_time': game.get_remaining_time(), 'time_up': game.time_up, 'game_over': game.game_over, 'state': game.get_state()})

@socketio.on('create_room')
def handle_create_room(data):
    room_code = generate_room_code()
    grid_size = data.get('grid_size', 5)
    game = DotsBoxesGame(grid_size=grid_size, ai_skill=0, time_limit=0)
    active_rooms[room_code] = {
        'game': game, 'mode': 'multiplayer', 'players': [request.sid],
        'player_ids': {request.sid: 1}, 'created_at': datetime.now()
    }
    join_room(room_code)
    emit('room_created', {'room_code': room_code, 'player_number': 1, 'state': game.get_state()})

@socketio.on('join_room')
def handle_join_room(data):
    room_code = data.get('room_code')
    if room_code not in active_rooms:
        emit('error', {'message': 'Room not found'})
        return
    
    room = active_rooms[room_code]
    if len(room['players']) >= 2:
        emit('error', {'message': 'Room is full'})
        return
    
    room['players'].append(request.sid)
    room['player_ids'][request.sid] = 2
    join_room(room_code)
    emit('room_joined', {'room_code': room_code, 'player_number': 2, 'state': room['game'].get_state()})
    emit('opponent_joined', {'player_number': 2}, room=room_code, skip_sid=request.sid)

@socketio.on('multiplayer_move')
def handle_multiplayer_move(data):
    room_code = data.get('room_code')
    line_type = data.get('line_type')
    i = data.get('i')
    j = data.get('j')
    
    if room_code not in active_rooms:
        emit('error', {'message': 'Room not found'})
        return
    
    room = active_rooms[room_code]
    player_number = room['player_ids'].get(request.sid)
    
    if not player_number:
        emit('error', {'message': 'You are not in this game'})
        return
    
    game = room['game']
    if game.current_player != player_number:
        emit('error', {'message': 'Not your turn'})
        return
    
    result = game.make_move(line_type, i, j, player_number)
    if not result['success']:
        emit('error', {'message': result['message']})
        return
    
    emit('move_made', {
        'state': game.get_state(),
        'move': {'line_type': line_type, 'i': i, 'j': j, 'player': player_number},
        'result': result
    }, room=room_code)

@socketio.on('leave_room')
def handle_leave_room(data):
    room_code = data.get('room_code')
    if room_code in active_rooms:
        leave_room(room_code)

@socketio.on('disconnect')
def handle_disconnect():
    for room_code, room_data in list(active_rooms.items()):
        if 'players' in room_data and request.sid in room_data['players']:
            emit('opponent_disconnected', room=room_code, skip_sid=request.sid)
            if room_code in active_rooms:
                del active_rooms[room_code]
            break

if __name__ == '__main__':
    socketio.run(app, debug=True)

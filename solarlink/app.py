from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from config import Config
import os

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    energy_produced = db.Column(db.Float, default=0.0)
    energy_consumed = db.Column(db.Float, default=0.0)
    credits = db.Column(db.Float, default=100.0)  # Starting credits
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_excess_energy(self):
        return max(0, self.energy_produced - self.energy_consumed)
    
    def get_deficit_energy(self):
        return max(0, self.energy_consumed - self.energy_produced)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    energy_units = db.Column(db.Float, nullable=False)
    credits_used = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))
        
        user = User(username=username, email=email)
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        
        flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST':
        produced = float(request.form['energy_produced'])
        consumed = float(request.form['energy_consumed'])
        
        current_user.energy_produced = produced
        current_user.energy_consumed = consumed
        
        # Update credits based on excess/deficit
        excess = current_user.get_excess_energy()
        if excess > 0:
            # Earn credits for excess energy
            current_user.credits += excess * 10  # 10 credits per kWh excess
        
        db.session.commit()
        flash('Energy data updated successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('dashboard.html', user=current_user)

@app.route('/marketplace')
@login_required
def marketplace():
    # Get all users except current user
    all_users = User.query.filter(User.id != current_user.id).all()
    
    # Separate users with excess and deficit
    excess_users = []
    deficit_users = []
    
    for user in all_users:
        excess = user.get_excess_energy()
        deficit = user.get_deficit_energy()
        
        if excess > 0:
            excess_users.append({
                'id': user.id,
                'username': user.username,
                'excess': excess,
                'credits': user.credits
            })
        elif deficit > 0:
            deficit_users.append({
                'id': user.id,
                'username': user.username,
                'deficit': deficit,
                'credits': user.credits
            })
    
    return render_template('marketplace.html', 
                         excess_users=excess_users, 
                         deficit_users=deficit_users)

@app.route('/transfer', methods=['POST'])
@login_required
def transfer():
    receiver_id = request.form.get('receiver_id')
    energy_units = float(request.form.get('energy_units', 0))
    
    if energy_units <= 0:
        flash('Invalid energy amount', 'danger')
        return redirect(url_for('marketplace'))
    
    receiver = User.query.get(receiver_id)
    if not receiver:
        flash('User not found', 'danger')
        return redirect(url_for('marketplace'))
    
    # Check if sender has enough excess energy
    if current_user.get_excess_energy() < energy_units:
        flash('You do not have enough excess energy', 'danger')
        return redirect(url_for('marketplace'))
    
    # Calculate credits (1 credit per kWh)
    credits_used = energy_units
    
    # Check if receiver has enough credits
    if receiver.credits < credits_used:
        flash('Receiver does not have enough credits', 'danger')
        return redirect(url_for('marketplace'))
    
    # Perform transfer
    current_user.energy_produced -= energy_units  # Reduce produced energy
    current_user.credits += credits_used  # Earn credits
    
    receiver.energy_consumed -= energy_units  # Reduce consumption need
    receiver.credits -= credits_used  # Spend credits
    
    # Create transaction record
    transaction = Transaction(
        sender_id=current_user.id,
        receiver_id=receiver.id,
        energy_units=energy_units,
        credits_used=credits_used
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    flash(f'Successfully transferred {energy_units} kWh to {receiver.username}', 'success')
    return redirect(url_for('marketplace'))

@app.route('/history')
@login_required
def history():
    # Get transactions where user is either sender or receiver
    sent_transactions = Transaction.query.filter_by(sender_id=current_user.id).all()
    received_transactions = Transaction.query.filter_by(receiver_id=current_user.id).all()
    
    # Combine and sort by timestamp
    all_transactions = sorted(
        sent_transactions + received_transactions,
        key=lambda x: x.timestamp,
        reverse=True
    )
    
    return render_template('history.html', transactions=all_transactions)

@app.route('/api/user/<int:user_id>')
def get_user(user_id):
    user = User.query.get(user_id)
    if user:
        return jsonify({
            'username': user.username,
            'excess': user.get_excess_energy(),
            'deficit': user.get_deficit_energy(),
            'credits': user.credits
        })
    return jsonify({'error': 'User not found'}), 404

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
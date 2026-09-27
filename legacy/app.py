from flask import Flask, request, render_template
import joblib
import pandas as pd
from geopy.distance import geodesic
import plotly.graph_objects as go

# Initialize Flask app
app = Flask(__name__)

# Load models and label encoder
model_reg = joblib.load("model.pkl")
model_clf = joblib.load("model_clf.pkl")
label_encoder = joblib.load("label_encoder.pkl")


@app.route("/")
def home():
    return render_template("home_page.html")

@app.route("/login_page.html")
def login():
    return render_template("login_page.html")

@app.route("/signup_page.html")
def signup():
    return render_template("signup_page.html")

@app.route("/dashboard.html")
def dashboard():
    return render_template("dashboard.html")

@app.route("/register.html")
def vehicle_registration():
    return render_template("register.html")

@app.route("/driver_behavior.html")
def driver_behavior():
    return render_template("driver_behavior.html")

@app.route("/maintenance_alert.html")
def maintenance_alert():
    return render_template("maintenance_alert.html")

@app.route("/report_generation.html")
def report_generation():
    return render_template("report_generation.html")

# Define a route for the home page
@app.route("/battery_health.html", methods=["GET", "POST"])
def battery_health():
    if request.method == "POST":
        # Get form data
        try:
            battery_capacity = float(request.form["capacity"])
            cycle_count = int(request.form["cycle_count"])
            voltage = float(request.form["voltage"])
            temperature = float(request.form["temperature"])
            internal_resistance = float(request.form["internal_resistance"])

            # Create input DataFrame
            input_data = pd.DataFrame([{
                "Battery Capacity (mAh)": battery_capacity,
                "Cycle Count": cycle_count,
                "Voltage (V)": voltage,
                "Temperature (°C)": temperature,
                "Internal Resistance (mΩ)": internal_resistance
            }])

            # Make predictions
            predicted_health = model_reg.predict(input_data)[0]
            predicted_status_encoded = model_clf.predict(input_data)[0]
            predicted_status = label_encoder.inverse_transform([predicted_status_encoded])[0]

            # Render the results
            return render_template("battery_health.html", health=round(predicted_health, 2), status=predicted_status)

        except Exception as e:
            return render_template("battery_health.html", error=str(e))

    # For GET request, render the form
    return render_template("battery_health.html", health=None, status=None)

# Load EV Charging Stations data
ev_stations = pd.read_csv("datasets/India_EV_Charging_Stations.csv")

# Ensure columns have correct names and handle missing values
ev_stations.rename(columns=str.lower, inplace=True)
ev_stations.dropna(subset=['latitude', 'longitude'], inplace=True)

# Helper function to find EV stations near the route
def get_stations_near_route(source_coords, dest_coords, battery_health, stations):
    nearby_stations = []
    for _, station in stations.iterrows():
        station_coords = (station['latitude'], station['longitude'])
        # Check if the station is within a reasonable distance of the route
        if geodesic(source_coords, station_coords).km <= battery_health * 0.5 or \
           geodesic(dest_coords, station_coords).km <= battery_health * 0.5:
            nearby_stations.append({
                "name": station['name'],
                "address": station['address'],
                "city": station['city'],
                "state": station['state']
            })
    return nearby_stations

@app.route('/optimize_route.html', methods=['GET', 'POST'])
def optimize_route():
    if request.method == 'POST':
        # Get user input
        source_city = request.form.get('source_city')
        dest_city = request.form.get('dest_city')
        battery_health = int(request.form.get('battery_health'))
        
        # Validate inputs
        if not source_city or not dest_city or not battery_health:
            return render_template('optimize_route.html', error="Please fill out all fields.")
        
        # Simulate coordinates lookup (replace with real geocoding API if needed)
        source_coords = (28.6139, 77.2090)  # Example: New Delhi
        dest_coords = (19.0760, 72.8777)  # Example: Mumbai
        
        # Find EV stations near the route
        stations = get_stations_near_route(source_coords, dest_coords, battery_health, ev_stations)
        
        if not stations:
            return render_template('optimize_route.html', error="No stations found near the route.")
        
        return render_template('optimize_route.html', stations=stations)
    
    return render_template('optimize_route.html')

@app.route('/energy_consumption.html', methods=['GET'])
def energy_consumption():
    try:
        # Load and preprocess data
        df = pd.read_csv(r"./datasets/Energy_Consumption_Dataset.csv", parse_dates=['Datetime'], index_col='Datetime')
        print(df)
        df['COMED_MW'] = pd.to_numeric(df['COMED_MW'], errors='coerce')
        df['COMED_MW'] = df['COMED_MW'].fillna(0)

        # Calculate operational costs
        cost_per_MWh = 100
        df_sample = df.resample('D').sum()
        df_sample['Operational_Costs'] = df_sample['COMED_MW'] * cost_per_MWh

        # Create the Plotly graph
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_sample.index, y=df_sample['COMED_MW'], mode='lines', 
                                 name='Energy Consumption (MW)', line=dict(color='blue')))
        fig.add_trace(go.Scatter(x=df_sample.index, y=df_sample['Operational_Costs'], mode='lines', 
                                 name='Operational Costs ($)', line=dict(color='green')))
        fig.update_layout(title='Energy Consumption and Operational Costs Over Time',
                          xaxis_title='Date-Time', yaxis_title='Value',
                          legend_title='Metrics', template='plotly_white', 
                          xaxis=dict(tickformat="%Y-%m-%d"), height=600, width=1000)

        # Convert Plotly figure to HTML
        graph_html = fig.to_html(full_html=False)

        # Pass graph_html to the template
        return render_template('energy_consumption.html', graph_html=graph_html)
    
    except Exception as e:
        return render_template('energy_consumption.html', error=str(e))

if __name__ == "__main__":
    app.run(debug=True)

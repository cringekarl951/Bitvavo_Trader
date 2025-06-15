import os
import time
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from binance.client import Client
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
import threading
import logging
from datetime import datetime, timedelta
import pytz
import gspread
from google.oauth2.service_account import Credentials
import base64
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')
spreadsheet_id = os.getenv('GOOGLE_SPREADSHEET_ID')

# Initialize Binance client
client = Client(api_key, api_secret)

# Google Sheets authentication
def get_gspread_client():
    """Initialize gspread client using service account credentials."""
    try:
        # Decode base64-encoded service account JSON from environment variable
        creds_base64 = os.getenv('GOOGLE_CREDENTIALS')
        creds_json = base64.b64decode(creds_base64).decode('utf-8')
        creds_dict = json.loads(creds_json)
        
        # Create credentials
        scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        logging.info("Successfully authenticated with Google Sheets.")
        return gc
    except Exception as e:
        logging.error(f"Failed to authenticate with Google Sheets: {e}")
        raise

# API rate limit settings
RATE_LIMIT_PER_MINUTE = 1200  # Conservative estimate of weight units per minute
WEIGHT_THRESHOLD = RATE_LIMIT_PER_MINUTE * 0.9  # 90% of rate limit
current_weight = 0
lock = threading.Lock()
last_reset_time = time.time()

# Directory to save plots
os.makedirs('plots', exist_ok=True)

def reset_rate_limit():
    """Reset the rate limit counter every minute."""
    global current_weight, last_reset_time
    current_time = time.time()
    if current_time - last_reset_time >= 60:
        with lock:
            current_weight = 0
            last_reset_time = current_time
            logging.info("Rate limit counter reset.")

def check_rate_limit(request_weight):
    """Check if a request can be made without exceeding the rate limit."""
    global current_weight
    reset_rate_limit()
    with lock:
        if current_weight + request_weight > WEIGHT_THRESHOLD:
            sleep_time = 60 - (time.time() - last_reset_time)
            if sleep_time > 0:
                logging.info(f"Rate limit approaching. Sleeping for {sleep_time:.2f} seconds.")
                time.sleep(sleep_time)
                reset_rate_limit()
        current_weight += request_weight

def get_top_liquid_coins(n=100):
    """Get the top N coins by 24-hour trading volume."""
    check_rate_limit(40)  # Weight for /api/v3/ticker/24hr
    tickers = client.get_ticker()
    # Filter for USDT pairs and sort by volume
    usdt_pairs = [ticker for ticker in tickers if ticker['symbol'].endswith('USDT')]
    sorted_tickers = sorted(usdt_pairs, key=lambda x: float(x['quoteVolume']), reverse=True)
    top_coins = [ticker['symbol'] for ticker in sorted_tickers[:n]]
    logging.info(f"Retrieved {len(top_coins)} liquid coins.")
    return top_coins

def fetch_candlestick_data(symbol):
    """Fetch 1-minute candlestick data for the last 24 hours for a given symbol."""
    try:
        check_rate_limit(1)  # Weight for /api/v3/klines
        end_time = datetime.now(pytz.UTC)
        start_time = end_time - timedelta(days=1)
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_1MINUTE,
            start_str=int(start_time.timestamp() * 1000),
            end_str=int(end_time.timestamp() * 1000)
        )
        # Convert to DataFrame
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df['close'] = df['close'].astype(float)
        logging.info(f"Fetched data for {symbol}")
        return symbol, df
    except Exception as e:
        logging.error(f"Error fetching data for {symbol}: {e}")
        return symbol, None

def calculate_volatility(df):
    """Calculate volatility as the standard deviation of log returns."""
    if df is None or df.empty:
        return np.nan
    df['log_return'] = np.log(df['close'] / df['close'].shift(1))
    volatility = df['log_return'].std() * np.sqrt(1440)  # Annualized for 1-minute data
    return volatility

def save_to_google_sheets(top_10_volatile):
    """Save top 10 volatile coins to Google Spreadsheet, clearing old data."""
    try:
        gc = get_gspread_client()
        spreadsheet = gc.open("Trade_Management")
        worksheet = spreadsheet.worksheet("Volatile_Coins")
        
        # Clear existing data in the sheet
        worksheet.clear()
        
        # Prepare data with timestamp
        timestamp = datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S')
        headers = ['Timestamp', 'Symbol', 'Volatility']
        data = [[timestamp, symbol, volatility] for symbol, volatility in top_10_volatile]
        
        # Append headers and data
        worksheet.append_rows([headers] + data)
        logging.info("Successfully cleared and updated Volatile_Coins sheet in Trade_Management spreadsheet.")
    except Exception as e:
        logging.error(f"Failed to update Google Spreadsheet: {e}")
        raise

def plot_price_courses(symbols, title, filename):
    """Plot price courses for given symbols and save the plot."""
    plt.figure(figsize=(12, 8))
    for symbol, df in symbols:
        plt.plot(df['open_time'], df['close'], label=symbol)
    plt.title(title)
    plt.xlabel('Time')
    plt.ylabel('Price (USDT)')
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(f'plots/{filename}.png')
    plt.close()
    logging.info(f"Saved plot: {filename}")

def main():
    # Step 1: Get top 100 liquid coins
    symbols = get_top_liquid_coins(100)
    
    # Step 2: Fetch candlestick data using multithreading
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_candlestick_data, symbol) for symbol in symbols]
        for future in futures:
            results.append(future.result())
    
    # Step 3: Calculate volatility for each coin
    volatilities = []
    for symbol, df in results:
        if df is not None:
            volatility = calculate_volatility(df)
            volatilities.append((symbol, volatility))
    
    # Step 4: Sort by volatility
    volatilities = sorted(volatilities, key=lambda x: x[1], reverse=True)
    top_10_volatile = [(x[0], x[1]) for x in volatilities[:10] if not np.isnan(x[1])]
    bottom_10_volatile = [(x[0], x[1]) for x in volatilities[-10:] if not np.isnan(x[1])]
    
    # Step 5: Save top 10 volatile coins to Google Sheets
    if top_10_volatile:
        save_to_google_sheets(top_10_volatile)
    
    # Step 6: Plot price courses
    if top_10_volatile:
        top_10_data = [(symbol, df) for symbol, df in results if symbol in [x[0] for x in top_10_volatile]]
        plot_price_courses(top_10_data, 'Top 10 Most Volatile Coins', 'top_10_volatile')
    if bottom_10_volatile:
        bottom_10_data = [(symbol, df) for symbol, df in results if symbol in [x[0] for x in bottom_10_volatile]]
        plot_price_courses(bottom_10_data, 'Top 10 Least Volatile Coins', 'bottom_10_volatile')
    
    logging.info("Script completed successfully.")

if __name__ == '__main__':
    main()
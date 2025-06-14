#!/usr/bin/env python3
import os
from python_bitvavo_api.bitvavo import Bitvavo
import logging
from telegram import Bot
import asyncio
from datetime import datetime

# Set up logging to stdout (for GitHub Actions logs)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def get_bitvavo_portfolio():
    """Retrieves Bitvavo portfolio and calculates values."""
    try:
        api_key = os.getenv('BITVAVO_API_KEY')
        api_secret = os.getenv('BITVAVO_API_SECRET')
        
        if not api_key or not api_secret:
            raise ValueError("Bitvavo API key or secret not found in environment variables.")
        
        bitvavo = Bitvavo({'APIKEY': api_key, 'APISECRET': api_secret})
        logger.info("Bitvavo client initialized successfully.")
        
        result = {
            "portfolio_value_eur": 0.0,
            "asset_values": [],
            "rate_limit_remaining": None
        }
        
        balance = bitvavo.balance({})
        logger.info("Retrieved balance information.")
        
        total_portfolio_value = 0.0
        asset_values = []
        
        for asset in balance:
            symbol = asset["symbol"]
            available = float(asset["available"])
            in_order = float(asset["inOrder"])
            total_amount = available + in_order
            
            value_eur = 0.0
            if symbol == "EUR":
                value_eur = available
                total_portfolio_value += available
            else:
                try:
                    market = f"{symbol}-EUR"
                    price_data = bitvavo.tickerPrice({'market': market})
                    if 'price' in price_data:
                        price = float(price_data['price'])
                        value_eur = total_amount * price
                        total_portfolio_value += value_eur
                    else:
                        logger.warning(f"No price data for {market}.")
                except Exception as e:
                    logger.error(f"Error fetching price for {market}: {str(e)}")
            
            asset_values.append([symbol, total_amount, value_eur])
        
        result["portfolio_value_eur"] = total_portfolio_value
        result["asset_values"] = asset_values
        result["rate_limit_remaining"] = bitvavo.getRemainingLimit()
        
        logger.info(f"Portfolio value: {total_portfolio_value:.2f} EUR")
        return result
    
    except Exception as e:
        logger.error(f"Error retrieving portfolio: {str(e)}")
        return {"error": str(e)}

async def send_to_telegram(portfolio_data):
    """Sends portfolio data to Telegram chat."""
    try:
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if not bot_token or not chat_id:
            raise ValueError("Telegram bot token or chat ID not found in environment variables.")
        
        bot = Bot(token=bot_token)
        
        # Format message
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"📊 *Bitvavo Portfolio Update* ({timestamp})\n\n"
        message += f"💰 *Total Portfolio Value*: {portfolio_data['portfolio_value_eur']:.2f} EUR\n\n"
        message += "📈 *Asset Details*:\n"
        for asset in portfolio_data["asset_values"]:
            symbol, amount, value = asset
            message += f"- {symbol}: {amount:.6f} ({value:.2f} EUR)\n"
        message += f"\n🔒 *Remaining Rate Limit*: {portfolio_data['rate_limit_remaining']}"
        
        # Send message
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        logger.info("Portfolio data sent to Telegram.")
    
    except Exception as e:
        logger.error(f"Error sending to Telegram: {str(e)}")

async def main():
    portfolio_data = await get_bitvavo_portfolio()
    if "error" not in portfolio_data:
        await send_to_telegram(portfolio_data)
    else:
        logger.error(f"Failed to retrieve portfolio: {portfolio_data['error']}")

if __name__ == "__main__":
    asyncio.run(main())


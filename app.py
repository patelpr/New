from flask import Flask, request, jsonify
import logging
from datetime import datetime, timedelta
import os
import json
import aiohttp
import urllib.parse
from functools import wraps
from store_mappings import get_store_id_from_zendesk 
import asyncio

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Token management
class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = None

    async def get_valid_token(self):
        """Get a valid token, refreshing if necessary"""
        if not self.token or self.is_expired():
            await self.refresh_token()
        return self.token

    def is_expired(self):
        """Check if the current token is expired"""
        return not self.expires_at or datetime.now() >= self.expires_at

    async def refresh_token(self):
        """Refresh the authentication token"""
        try:
            token_url = "https://www.mybpsphotos.com/api/auth/v1/token"
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            data = {
                "grant_type": "client_credentials",
                "client_id": os.getenv('CLIENT_ID', "productionWS@pictureu.com:9999"),
                "client_secret": os.getenv('CLIENT_SECRET', "production2016")
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(token_url, headers=headers, data=data) as response:
                    response.raise_for_status()
                    token_data = await response.json()
                    
                    self.token = token_data["access_token"]
                    # Set token expiration (assuming 1 hour validity, adjust as needed)
                    self.expires_at = datetime.now() + timedelta(hours=1)
                    logger.info("Token refreshed successfully")
                    
        except Exception as e:
            logger.error(f"Failed to refresh token: {str(e)}")
            raise

token_manager = TokenManager()
import base64
from zendesk_api import ZendeskAPI  # We'll need to create this

async def format_photo_data_html(photo_data):
    """Format photo data into HTML"""
    if not photo_data:
        return "<p>No photo data found</p>"
        
    # Start with the customer greeting
    html = """
    <div style="font-family: Arial, sans-serif;">
    <p>Dear Customer,</p>
    <p>Thank you for contacting Bass Pro Photo Support!</p>
    """
    
    for photo in photo_data["data"]["PhotoSubjects"]:
        sitting_id = photo.get('SittingIdentifier', 'N/A')
        claim_url = photo.get('ClaimUrl', '')

        
        # Check if there's an OriginalURL with ts parameter > 0
        original_url = photo["Pictures"][0].get('OriginalURL', '')
        screen_url = photo["Pictures"][0].get('ScreenURL', '')
        if original_url:
            try:
                        
                html += f"""
                <p>SecureCode: <strong>{sitting_id}</strong></p>
                <p><strong>Add To Account: <a href="{claim_url}">{claim_url}</a></strong> 
                """
                # Parse URL and check ts parameter
                parsed_url = urllib.parse.urlparse(original_url)
                params = urllib.parse.parse_qs(parsed_url.query)
                ts = params.get('ts', ['0'])[0]
                
                if int(ts) > 0:
                    html += f"""
                    <p>Thank you for purchasing a package at the Store.</p>
                    <p><strong><a href="{original_url}">{original_url}</a></strong> 
                    <img src="{original_url}" alt="Photo" style="width: 700px; height: auto;">
                    """
                else:
                    html += f"""
                    <p> It appears your image is not marked purchased, yet please provide your receipt to unlock. </p>
                    <p> Here is a lower resolution image of your photo</p>
                    <p><strong><a href="{screen_url}">{screen_url}</a></strong> 
                    <img src="{screen_url}" alt="Photo" style="width: 700px; height: auto;">
                    """
            except (ValueError, AttributeError):
                pass  # Skip if URL parsing fails
    
    html += "</div>"
    return html

async def update_zendesk_ticket(ticket_id, photo_data):
    """Update Zendesk ticket with photo data"""
    try:
        # Initialize Zendesk API client
        zendesk = ZendeskAPI(
            subdomain='pictureu',
            email='ppatel@pictureu.com',
            token='W4OaEz8b1pHzUZQQ531ytf9Lcp4zQiTA8rse2mRv'
        )
        
        # Format photo data as HTML
        html_content = await format_photo_data_html(photo_data)
        
        # Prepare ticket update data
        ticket_data = {
            "ticket": {
                "comment": {
                    "public": False,
                    "html_body": html_content
                }
            }
        }
        
        # Update the ticket
        response = await zendesk.update_ticket(ticket_id, ticket_data)
        logger.info(f"Successfully updated Zendesk ticket {ticket_id}")
        return response
        
    except Exception as e:
        logger.error(f"Failed to update Zendesk ticket {ticket_id}: {str(e)}")
        raise

async def get_by_secure_code(store, securecode):
    """Search for photos using store and secure code"""
    try:
        token = await token_manager.get_valid_token()
        search_url = f"https://www.mybpsphotos.com/api/picturearchive/v1/photosubjects/{store}/search"
        params = {"sittingidentifier": securecode}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, headers=headers, params=params) as response:
                response.raise_for_status()
                photo_data = await response.json()
                return photo_data
                
    except Exception as e:
        logger.error(f"Failed to get photos by secure code: {str(e)}")
        raise

async def get_by_email(store, email):
    """Search for photos using store and secure code"""
    try:
        token = await token_manager.get_valid_token()
        search_url = f"https://www.mybpsphotos.com/api/picturearchive/v1/photosubjects/{store}/search"
        params = {"email": email}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, headers=headers, params=params) as response:
                response.raise_for_status()
                photo_data = await response.json()
                return photo_data
                
    except Exception as e:
        logger.error(f"Failed to get photos by secure code: {str(e)}")
        raise

async def get_photos(store, securecode=None, email=None):
    """Search for photos using store and either secure code or email or both"""
    unique_photos = {}  # Using dict to track unique photos by SittingIdentifier
    
    try:
        token = await token_manager.get_valid_token()
        search_url = f"https://www.mybpsphotos.com/api/picturearchive/v1/photosubjects/{store}/search"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Create tasks for both searches
        async with aiohttp.ClientSession() as session:
            search_tasks = []
            
            if securecode:
                params_code = {"sittingidentifier": securecode}
                search_tasks.append(session.get(search_url, headers=headers, params=params_code))
            
            if email:
                params_email = {"email": email}
                search_tasks.append(session.get(search_url, headers=headers, params=params_email))
            
            # Execute all searches concurrently
            responses = await asyncio.gather(*search_tasks, return_exceptions=True)
            
            # Process responses
            for response in responses:
                if isinstance(response, Exception):
                    logger.error(f"Search failed: {str(response)}")
                    continue
                    
                response.raise_for_status()
                photo_data = await response.json()
                
                # Add unique photos to our collection
                if "data" in photo_data and "PhotoSubjects" in photo_data["data"]:
                    for photo in photo_data["data"]["PhotoSubjects"]:
                        sitting_id = photo.get('SittingIdentifier')
                        if sitting_id and sitting_id not in unique_photos:
                            unique_photos[sitting_id] = photo
        
        # Format response similar to original API response
        return {
            "data": {
                "PhotoSubjects": list(unique_photos.values())
            }
        }
                
    except Exception as e:
        logger.error(f"Failed to get photos: {str(e)}")
        raise

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'message': 'Server is running'
    }), 200

@app.route('/webhook/zendesk', methods=['GET', 'POST'])
async def zendesk_webhook():
    if request.method == 'GET':
        return jsonify({
            'status': 'ready',
            'timestamp': datetime.now().isoformat()
        }), 200

    try:
        # Get and parse JSON data
        try:
            data = request.get_json(force=True)
            logger.info(f"Received data: {data}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON data: {str(e)}")
            return jsonify({
                'status': 'error',
                'message': 'Invalid JSON data'
            }), 400
                
        zendesk_store = data.get('store')
        if zendesk_store:
            data['store'] = get_store_id_from_zendesk(zendesk_store)
        else:
            data['store'] = None

        # Only fetch photo data if store and either secure code or email are present
        if data.get('store') and (data.get('securecode') or data.get('email')):
            try:
                photo_subjects = await get_photos(
                    data['store'],
                    securecode=data.get('securecode'),
                    email=data.get('email')
                )
                data['photo_data'] = photo_subjects
                logger.info(f"Retrieved photo data for store {data['store']}")

                # Update Zendesk ticket with photo data
                if data.get('ticketid') and data['photo_data']:
                    await update_zendesk_ticket(data['ticketid'], data['photo_data'])

            except Exception as e:
                logger.error(f"Error retrieving photo data: {str(e)}")
                data['photo_data_error'] = str(e)
        
        response = {
            'status': 'success',
            'received_at': datetime.now().isoformat(),
            'ticket_info': data,
        }
        
        logger.info(f"Processed ticket info: {data}")
        return jsonify(response), 200
        
    except Exception as e:
        error_response = {
            'status': 'error',
            'message': str(e),
            'received_at': datetime.now().isoformat()
        }
        logger.error(f"Error processing webhook: {str(e)}")
        return jsonify(error_response), 400

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
from flask import Flask, request, jsonify
import logging
from datetime import datetime, timedelta
import os
import json
import aiohttp
import urllib.parse
import asyncio
from store_mappings import get_store_id_from_zendesk
from zendesk_api import ZendeskAPI

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = None

    async def get_valid_token(self):
        if not self.token or datetime.now() >= self.expires_at:
            await self.refresh_token()
        return self.token

    async def refresh_token(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://www.mybpsphotos.com/api/auth/v1/token",
                    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "client_credentials",
                          "client_id": os.getenv('CLIENT_ID', "productionWS@pictureu.com:9999"),
                          "client_secret": os.getenv('CLIENT_SECRET', "production2016")}
                ) as response:
                    response.raise_for_status()
                    token_data = await response.json()
                    self.token = token_data["access_token"]
                    self.expires_at = datetime.now() + timedelta(hours=1)
        except Exception as e:
            logger.error(f"Failed to refresh token: {e}")
            raise

token_manager = TokenManager()

async def get_all_partners():
    """Fetch all partners"""
    try:
        token = await token_manager.get_valid_token()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://www.mybpsphotos.com/api/partner/v1",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    return (await response.json()).get("data", {}).get("Partners", [])
                return []
    except Exception as e:
        logger.error(f"Error fetching partners: {e}")
        return []

async def search_photos(partner_id, securecode=None, email=None):
    """Search photos for a specific partner"""
    try:
        token = await token_manager.get_valid_token()
        params = {"sittingidentifier": securecode} if securecode else {"email": email}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://www.mybpsphotos.com/api/picturearchive/v1/photosubjects/{partner_id}/search",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params=params
            ) as response:
                return await response.json() if response.status == 200 else {"data": {"PhotoSubjects": []}}
    except Exception as e:
        logger.error(f"Search failed for partner {partner_id}: {e}")
        return {"data": {"PhotoSubjects": []}}

async def search_all_partners(securecode=None, email=None):
    """Search across all partners if partnerId is not provided"""
    partners = await get_all_partners()
    tasks = [search_photos(p["PartnerID"], securecode, email) for p in partners if "PartnerID" in p]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    photo_subjects = []
    for result in results:
        if isinstance(result, dict):
            photo_subjects.extend(result.get("data", {}).get("PhotoSubjects", []))

    return {"data": {"PhotoSubjects": photo_subjects}}

async def format_gallery(photo_data):
    """Convert photo data into a gallery format"""
    if not photo_data or "data" not in photo_data:
        return "<p>No photos found</p>"

    gallery_html = '<div style="display: flex; flex-wrap: wrap;">'
    for photo in photo_data["data"]["PhotoSubjects"]:
        sitting_id = photo.get("SittingIdentifier", "N/A")
        original_url, screen_url = "", ""

        for pic in photo.get("Pictures", []):
            url = pic.get("OriginalURL", "")
            ts = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("ts", [0])[0])
            if ts > 0:
                original_url = url
            else:
                screen_url = pic.get("ScreenURL", "")

        display_url = original_url if original_url else screen_url
        if display_url:
            gallery_html += f"""
                <div style="margin: 10px;">
                    <p>Sitting ID: <strong>{sitting_id}</strong></p>
                    <img src="{display_url}" alt="Photo" style="width: 300px; height: auto;">
                </div>
            """

    gallery_html += "</div>"
    return gallery_html
async def flatten_photo_data(photo_data):
    """Flatten the photo data to a single array of image objects."""
    flat_photos = []
    
    if not photo_data or "data" not in photo_data or "PhotoSubjects" not in photo_data["data"]:
        return []

    for subject in photo_data["data"]["PhotoSubjects"]:
        claim_url = subject.get("ClaimUrl", "")
        create_date = subject.get("CreateDate", "")
        partner_id = subject.get("PartnerID", "")
        photo_subject_id = subject.get("PhotoSubjectID", "")
        sitting_identifier = subject.get("SittingIdentifier", "")
 

        for picture in subject.get("Pictures", []):
            purchase_flag = 0
            ts = int(urllib.parse.parse_qs(urllib.parse.urlparse(picture.get("OriginalURL", "")).query).get("ts", [0])[0])
            if ts > 0:
                purchase_flag = 1
            flat_photos.append({
                "ClaimUrl": claim_url,
                "CreateDate": create_date,
                "PartnerID": partner_id,
                "PhotoSubjectID": photo_subject_id,
                "SittingIdentifier": sitting_identifier,
                "ThumbnailURL": picture.get("ThumbnailURL", ""),
                "ScreenURL": picture.get("ScreenURL", ""),
                "PictureKey": picture.get("PictureKey", ""),
                "OriginalURL": picture.get("OriginalURL", ""),
                "GroupKey": picture.get("GroupKey", ""),
                "Paid" : purchase_flag,
            })

    return flat_photos


async def update_zendesk_ticket(ticket_id, photo_data):
    """Update Zendesk ticket with formatted gallery"""
    try:
        zendesk = ZendeskAPI(
            subdomain="pictureu",
            email="ppatel@pictureu.com",
            token="W4OaEz8b1pHzUZQQ531ytf9Lcp4zQiTA8rse2mRv"
        )
        html_content = await format_gallery(photo_data)
        return await zendesk.update_ticket(ticket_id, {"ticket": {"comment": {"public": False, "html_body": html_content}}})
    except Exception as e:
        logger.error(f"Error updating Zendesk ticket {ticket_id}: {e}")
        

@app.route('/search', methods=['GET'])
async def search_photos_route():
    """Single endpoint to search by securecode or email, optionally with partnerId"""
    partner_id = request.args.get("partnerId")
    securecode = request.args.get("securecode")
    email = request.args.get("email")

    if not securecode and not email:
        return jsonify({"status": "error", "message": "Provide securecode or email"}), 400

    try:
        # Get the photo data (nested)
        raw_photo_data = await (search_photos(partner_id, securecode, email) if partner_id else search_all_partners(securecode, email))

        # Flatten the data
        flat_results = await flatten_photo_data(raw_photo_data)

        return jsonify({
            "status": "success",
            "results_count": len(flat_results),
            "results": flat_results
        }), 200
    except Exception as e:
        logger.error(f"Error in search: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route('/webhook/zendesk', methods=['POST'])
async def zendesk_webhook():
    """Webhook to update Zendesk ticket with gallery images"""
    try:
        data = request.get_json(force=True)
        store = get_store_id_from_zendesk(data.get("store", ""))
        photo_data = await search_photos(store, data.get("securecode"), data.get("email")) if store else {}

        if data.get("ticketid") and photo_data.get("data", {}).get("PhotoSubjects"):
            await update_zendesk_ticket(data["ticketid"], photo_data)

        return jsonify({"status": "success", "ticket_info": data}), 200
    except Exception as e:
        logger.error(f"Error processing Zendesk webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

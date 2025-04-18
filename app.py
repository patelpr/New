import os
import io
import json
import asyncio
import logging
import requests
import urllib.parse
from datetime import datetime, timedelta
from tempfile import NamedTemporaryFile
from flask import Flask, request, send_file, abort, jsonify
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
from PIL import Image
import aiohttp

# Custom modules
from store_mappings import get_store_id_from_zendesk
from zendesk_api import ZendeskAPI

app = Flask(__name__)

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Token manager
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
                    data={
                        "grant_type": "client_credentials",
                        "client_id": os.getenv("CLIENT_ID", "productionWS@pictureu.com:9999"),
                        "client_secret": os.getenv("CLIENT_SECRET", "production2016")
                    }
                ) as response:
                    response.raise_for_status()
                    token_data = await response.json()
                    self.token = token_data["access_token"]
                    self.expires_at = datetime.now() + timedelta(hours=1)
        except Exception as e:
            logger.error(f"Failed to refresh token: {e}")
            raise

token_manager = TokenManager()

# Partner and photo searching
async def get_all_partners():
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
    partners = await get_all_partners()
    tasks = [search_photos(p["PartnerID"], securecode, email) for p in partners if "PartnerID" in p]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    photo_subjects = []
    for result in results:
        if isinstance(result, dict):
            photo_subjects.extend(result.get("data", {}).get("PhotoSubjects", []))

    return {"data": {"PhotoSubjects": photo_subjects}}

# Photo formatting
async def format_gallery(photo_data):
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

        display_url = original_url or screen_url
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
    flat_photos = []
    subjects = photo_data.get("data", {}).get("PhotoSubjects", [])

    for subject in subjects:
        for picture in subject.get("Pictures", []):
            ts = int(urllib.parse.parse_qs(urllib.parse.urlparse(picture.get("OriginalURL", "")).query).get("ts", [0])[0])
            flat_photos.append({
                "ClaimUrl": subject.get("ClaimUrl", ""),
                "CreateDate": subject.get("CreateDate", ""),
                "PartnerID": subject.get("PartnerID", ""),
                "PhotoSubjectID": subject.get("PhotoSubjectID", ""),
                "SittingIdentifier": subject.get("SittingIdentifier", ""),
                "ThumbnailURL": picture.get("ThumbnailURL", ""),
                "ScreenURL": picture.get("ScreenURL", ""),
                "PictureKey": picture.get("PictureKey", ""),
                "OriginalURL": picture.get("OriginalURL", ""),
                "GroupKey": picture.get("GroupKey", ""),
                "Paid": int(ts > 0),
            })

    return flat_photos

# Zendesk
async def update_zendesk_ticket(ticket_id, photo_data):
    try:
        zendesk = ZendeskAPI(
            subdomain="pictureu",
            email="ppatel@pictureu.com",
            token="W4OaEz8b1pHzUZQQ531ytf9Lcp4zQiTA8rse2mRv"
        )
        html_content = await format_gallery(photo_data)
        return await zendesk.update_ticket(ticket_id, {
            "ticket": {
                "comment": {
                    "public": False,
                    "html_body": html_content
                }
            }
        })
    except Exception as e:
        logger.error(f"Error updating Zendesk ticket {ticket_id}: {e}")

# Video injection
def inject_image_to_video(video_path, image_path, start_time, duration):
    video = VideoFileClip(video_path)
    img = Image.open(image_path)

    img_clip = ImageClip(image_path).set_duration(duration).resize(height=video.h).set_position("center")
    overlay = img_clip.set_start(start_time)

    final = CompositeVideoClip([video, overlay]).set_duration(video.duration)

    buffer = io.BytesIO()
    with NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
        final.write_videofile(temp_file.name, codec="libx264", audio_codec="aac", verbose=False, logger=None)
        temp_file.seek(0)
        buffer.write(temp_file.read())
        buffer.seek(0)
    return buffer

# Routes
@app.route('/makevideo')
def make_video():
    securecode = request.args.get("securecode")
    partnerid = request.args.get("partnerid")
    start = float(request.args.get("start", 4.7))
    duration = float(request.args.get("duration", 3.8))

    if not (securecode and partnerid):
        return abort(400, description="Missing securecode or partnerid.")

    try:
        photo_data = asyncio.run(search_photos(partnerid, securecode=securecode))
        photo_subjects = photo_data.get("data", {}).get("PhotoSubjects", [])

        if not photo_subjects or not photo_subjects[0]["Pictures"]:
            return abort(404, description="No image found for this partner/securecode.")

        image_url = photo_subjects[0]["Pictures"][0]["OriginalURL"]
        img_response = requests.get(image_url)
        if img_response.status_code != 200:
            return abort(500, description="Failed to download image.")

        with NamedTemporaryFile(delete=False, suffix='.jpg') as temp_img:
            temp_img.write(img_response.content)
            temp_img_path = temp_img.name

        video_blob = inject_image_to_video("video.mp4", temp_img_path, start, duration)
        return send_file(video_blob, mimetype='video/mp4', as_attachment=False, download_name="result.mp4")

    except Exception as e:
        logger.error(f"Server error: {e}")
        return abort(500, description="Unexpected server error.")

@app.route('/search', methods=['GET'])
async def search_photos_route():
    partner_id = request.args.get("partnerId")
    securecode = request.args.get("securecode")
    email = request.args.get("email")

    if not securecode and not email:
        return jsonify({"status": "error", "message": "Provide securecode or email"}), 400

    try:
        raw_photo_data = await (search_photos(partner_id, securecode, email) if partner_id else search_all_partners(securecode, email))
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

# Run app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

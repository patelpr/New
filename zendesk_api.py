# zendesk_api.py
import aiohttp
import base64

class ZendeskAPI:
    def __init__(self, subdomain, email, token):
        self.subdomain = "pictureu"
        self.email = email
        self.token = token
        self.base_url = f"https://pictureu.zendesk.com/api/v2"
        
        # Create basic auth token
        auth_str = f"ppatel@pictureu.com/token:W4OaEz8b1pHzUZQQ531ytf9Lcp4zQiTA8rse2mRv"
        self.auth_token = base64.b64encode(auth_str.encode()).decode()
        
    async def update_ticket(self, ticket_id, data):
        """Update a Zendesk ticket"""
        url = f"{self.base_url}/tickets/{ticket_id}.json"
        headers = {
            "Authorization": f"Basic {self.auth_token}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=data) as response:
                response.raise_for_status()
                return await response.json()
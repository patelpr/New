from flask import Flask, request, jsonify
import logging
from datetime import datetime

app = Flask(__name__)

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

@app.route('/webhook/zendesk', methods=['POST'])
def zendesk_webhook():
    try:
        # Get the payload
        data = request.json
        
        # Log the entire payload
        logger.info("Received webhook data:")
        logger.info(data)
        
        # Create a response with the received data
        response = {
            'status': 'success',
            'received_at': datetime.now().isoformat(),
            'data': data
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f'Error processing webhook: {str(e)}')
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
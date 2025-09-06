from flask import Flask, send_file, request
from flask_restx import Api, Resource, fields, reqparse
from src.scripts.simple.top_speed import TopSpeedFunc
import os

app = Flask(__name__)
api = Api(app, version='1.0', title='F1 Telemetry API',
          description='API for Formula 1 Telemetry Data Analysis',
          doc='/docs')

ns = api.namespace('api', description='F1 Telemetry Operations')

# Request parser for common parameters
parser = reqparse.RequestParser()
parser.add_argument('year', type=int, default=2025, help='Year of the race')
parser.add_argument('gp', type=str, default='Dutch Grand Prix', help='Name of the Grand Prix')
parser.add_argument('session', type=str, default='Q', help='Session type (Q for qualifying)')

@ns.route('/health')
class HealthCheck(Resource):
    @api.doc(description='Check if the API is running')
    def get(self):
        return {"status": "healthy"}, 200

@ns.route('/quali/top-speed')
class QualiTopSpeed(Resource):
    @api.doc(description='Get top speed comparison plot',
             params={
                 'year': 'Year of the race',
                 'gp': 'Name of the Grand Prix',
                 'session': 'Session type (Q for qualifying)'
             },
             responses={
                 200: 'Success - Returns a PNG image',
                 500: 'Internal Server Error'
             })
    @api.expect(parser)
    def get(self):
        try:
            args = parser.parse_args()
            output_path = TopSpeedFunc(args['year'], args['gp'], args['session'])
            return send_file(output_path, mimetype='image/png')
        except Exception as e:
            api.abort(500, str(e))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

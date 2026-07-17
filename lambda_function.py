import json
import boto3
import uuid
import urllib.parse
from datetime import datetime

rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('ForestWildlifeLogs')

# Broad list of animals to categorize wildlife specifically
WILDLIFE_TARGETS = ["Deer", "Tiger", "Bear", "Elephant", "Leopard", "Wolf", "Fox", "Lion", "Monkey", "Bird", "Animal", "Mammal"]

def lambda_handler(event, context):
    print(f"Incoming Event Frame: {json.dumps(event)}")
    
    path = event.get('rawPath') or event.get('path') or ''
    method = event.get('requestContext', {}).get('http', {}).get('method') or event.get('httpMethod') or ''
    
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'OPTIONS,GET',
        'Access-Control-Allow-Headers': 'Content-Type'
    }

    if method == 'OPTIONS':
        return {'statusCode': 200, 'headers': headers, 'body': ''}

    try:
        # FLOW A: Automatic background S3 image processing upload trigger
        if 'Records' in event and 's3' in event['Records'][0]:
            record = event['Records'][0]
            bucket_name = record['s3']['bucket']['name']
            image_name = urllib.parse.unquote_plus(record['s3']['object']['key'])
            timestamp = datetime.utcnow().isoformat() + "Z"
            
            # Request computer vision label extraction
            rek_response = rekognition.detect_labels(
                Image={'S3Object': {'Bucket': bucket_name, 'Name': image_name}},
                MaxLabels=10,
                MinConfidence=75.0
            )
            
            detected_animal = "Unknown Species"
            highest_confidence = 0.0
            animal_count = 1  # Base threshold initialization
            
            # Check instances to identify individual animals present in the shot
            for label in rek_response.get('Labels', []):
                name = label['Name']
                confidence = label['Confidence']
                
                if any(target.lower() in name.lower() for target in WILDLIFE_TARGETS) and name.lower() != "animal":
                    detected_animal = name
                    highest_confidence = confidence
                    # If Rekognition identifies discrete shapes bounding boxes, count them
                    if label.get('Instances'):
                        animal_count = len(label['Instances'])
                    break
            
            # Write structured telemetry directly to DynamoDB
            table.put_item(
                Item={
                    'log_id': str(uuid.uuid4())[:8],
                    'image_name': image_name,
                    'animal_type': detected_animal.capitalize(),
                    'confidence': str(round(highest_confidence, 2)),
                    'animal_count': int(animal_count),
                    'timestamp': timestamp
                }
            )
            return {'status': 'Ingestion telemetry logged successfully.'}

        # FLOW B: HTTP API GET Request from the EC2 Website to load the Dashboard Data
        elif '/wildlife-summary' in path and method == 'GET':
            response = table.scan()
            items = response.get('Items', [])
            
            total_sightings_count = 0
            species_distribution_map = {}
            recent_logs = []
            
            # Sort chronologically descending
            items.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            for item in items:
                count = int(item.get('animal_count', 1))
                species = item.get('animal_type', 'Unknown')
                
                total_sightings_count += count
                species_distribution_map[species] = species_distribution_map.get(species, 0) + count
                
                if len(recent_logs) < 10:  # Restrict raw table list output view length
                    recent_logs.append({
                        'image': item.get('image_name'),
                        'species': species,
                        'confidence': item.get('confidence'),
                        'count': count,
                        'time': item.get('timestamp')
                    })
            
            payload = {
                'total_animals': total_sightings_count,
                'unique_species_count': len(species_distribution_map),
                'distribution': species_distribution_map,
                'history': recent_logs
            }
            return {'statusCode': 200, 'headers': headers, 'body': json.dumps(payload)}

        return {'statusCode': 404, 'headers': headers, 'body': json.dumps({'error': 'Resource path unmapped'})}
        
    except Exception as e:
        print(f"CRASH LOG: {str(e)}")
        return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}

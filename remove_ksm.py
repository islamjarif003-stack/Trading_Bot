import json

f = '/opt/trading-bot/dry_run_positions.json'
try:
    with open(f, 'r') as file:
        d = json.load(file)
    if 'KSMUSDT' in d:
        d.pop('KSMUSDT')
        print("Removed KSMUSDT")
        with open(f, 'w') as file:
            json.dump(d, file)
    else:
        print("KSMUSDT not found")
except Exception as e:
    print(e)

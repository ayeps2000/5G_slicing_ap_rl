import asyncio
import websockets
import json
import datetime
#todo implement interface/abstract method
class decision_module():
    def __init__(self):
        self.vnfid_timestamps = {}
        self.start()
        
    def start(self):
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(websockets.serve(self.up_server, "localhost", 8544))
        pending = asyncio.Task.all_tasks() #allow end the last task!
        loop.run_until_complete(asyncio.gather(*pending))
        loop.run_forever()


    async def send_message(self, message):
        uri = "ws://localhost:8765"
        async with websockets.connect(uri) as websocket:
            await websocket.send(json.dumps(message))


    async def up_server(self, websocket, path):
        print("receiving...")
        data = await websocket.recv()
        vnf_json_data = json.loads(data)
        print(vnf_json_data["vnf_id"])
        print(self.vnfid_timestamps.keys())
        if not vnf_json_data["vnf_id"] in self.vnfid_timestamps.keys():
            print("first time")
            self.vnfid_timestamps[vnf_json_data["vnf_id"]] = datetime.datetime.now()
            message = await self.scale_decision(vnf_json_data)
            await self.send_message(message)    
    
        elif(((datetime.datetime.now() - self.vnfid_timestamps[vnf_json_data["vnf_id"]]).total_seconds() / 60.0) > 1): 
            print("1 minute pass already... sending new order to scale....")
            message = await self.scale_decision(vnf_json_data)
            await self.send_message(message)
            self.vnfid_timestamps[vnf_json_data["vnf_id"]] = datetime.datetime.now()   
        else:
            print("discarting message...")

    async def scale_decision(self, message):
        await asyncio.sleep(3) 
        message["scale_decision"] = 0
        return message
        


if __name__ == "__main__":
    decision_module()
from osm_helper import OsmHelper
import requests
import argparse
import urllib3
from ObserverPattern.vnf_observer_pattern import VnfObserver as Observer
from ObserverPattern.vnf_observer_pattern import VnfCpuSubject as CpuSubject
import asyncio
import websockets
import json
from shutil import copyfile
import os
from vnf_scale_order_module import VnfScaleModule
from docker_supervisor import DockerSupervisor
import keys as keys
from utils.colors import bcolors 
import datetime


#vnf_manager
#current status: fixing the haproxy cfg file, in order to all the instances keep getting traffic despite the scale decision
#TODO Cancel event loop.
#TODO test feature scale up / scale down.
#TODO re factor code to acomplish CLEAN CODE
#TODO implements unit tests
class VnfManager(Observer):
    def __init__(self, base_url, sdm_ip, sdm_port):
        self.monitoringtime  = datetime.datetime.now()
        self.TAG = "VnfManager"
        self.load_balancer_docker_id = "haproxy" # it's the docker name of the load balancer 
        self.haproxy_cfg_name = "haproxy.cfg" #its the configuration filename
        self.sdm_port = sdm_port # port of the scale decision module 
        self.sdm_ip = sdm_ip # port of the scale decision module ip
        self.vnf_scale_module = VnfScaleModule()
        self.cadvisor_url =  base_url.replace("https","http")+":8080/api/v1.3/subcontainers/docker"
        self.osm_helper = OsmHelper(base_url+":9999/osm/")
        self.keys = keys.Keys()
        self.vnf_message = {}
        self.custom_print("init")
        self.custom_print("load balancer docker id: {}, cfg name: {}".format(self.load_balancer_docker_id, self.haproxy_cfg_name)) 
        asyncio.ensure_future(self.start(sdm_ip, base_url)) #main loop
        pending = asyncio.Task.all_tasks() 
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.gather(*pending))
        loop.run_forever()


    def custom_print(self, message,  mode=0):
        if mode is 0:
            print("{} Manager:{} {}".format( bcolors.OKBLUE, message, bcolors.ENDC))
        if mode is 1:
            print("{} Manager:    {} {}".format( bcolors.OKBLUE, message, bcolors.ENDC))
        
    async def start(self, sdm_ip, base_url):        
        self.base_url = base_url+":9999/osm/"  # osm nbi api.
        auth_token = self.osm_helper.get_osm_authentication_token()
        self.auth_token = auth_token
        #TODO cada vez que se actualize el load balancer, se debe actualizar el vnf_list.....
        ns_id_list, ns_vnf_list = self.osm_helper.get_nsid_list()
        asyncio.ensure_future(websockets.serve(self.server_function, "localhost", 8765))
        vnf_supervisor_instances = {}
        for ns_id, ns in ns_vnf_list.items():
            for vnf_index, vnf_id in ns["vnf"].items():
                self.custom_print("ns name:{} vnf:{}".format(ns_id, vnf_id),1)

                message = {
                    self.keys.flavor: "single", 
                    self.keys.volume: "small",
                    self.keys.ns_id: ns_id,
                    self.keys.vnf_id: vnf_id,
                    self.keys.vnf_index: vnf_index,
                    self.keys.sampling_time: 5,
                    self.keys.scale_decision: "scale_down"
                }
                await self.docker_process(message)
                await asyncio.sleep(10)
                self.vnf_message[vnf_id] = message 
        
        self.update_ips_lb() #TODO actualizar direcciones ip de las instancias creadas con docker 

        pending = asyncio.Task.all_tasks() # allow end the last task!
        self.custom_print("number of vnfs: {} current_tasks:{}".format(len(self.vnf_message),len(pending)))
        #loop.run_until_complete(asyncio.gather(*pending))
        for indx, instance in vnf_supervisor_instances.items():
            self.custom_print(self.TAG,"docker_id: {} vnf_id: {} docker_name:{}".format(
                instance.docker_id, instance.vnf_id, instance.docker_name), 1)
        #loop.run_forever()

    async def server_function(self, websockets, path):
        message = await websockets.recv()
        message = json.loads(message)
        self.custom_print("message received from sdm: {}".format(message),1)
        message = {
            self.keys.flavor: message[self.keys.flavor], 
            self.keys.volume: message[self.keys.volume],
            self.keys.ns_id: message[self.keys.ns_id],
            self.keys.vnf_id: message[self.keys.vnf_id],
            self.keys.vnf_index: message[self.keys.vnf_index],
            self.keys.sampling_time: message[self.keys.sampling_time],
            self.keys.scale_decision: message[self.keys.scale_decision]
            }
        """
            self.keys.cpu_load: message[self.keys.cpu_load],
            self.keys.rx_usage: message[self.keys.rx_usage],
            self.keys.tx_usage: message[self.keys.tx_usage],   
        """

        scale_rta = message[self.keys.scale_decision]
        if scale_rta == "no_scale":
            print("No debo hacer nada")

        else:
            await self.docker_process(message)
            self.update_ips_lb()
            pending = asyncio.Task.all_tasks()
            self.custom_print("current pending tasks:{}".format(len(pending)),1)

    async def docker_process(self, message):
        flavor = message[self.keys.flavor]
        volume = message[self.keys.volume]
        ns_id = message[self.keys.ns_id]
        vnf_id = message[self.keys.vnf_id]
        vnf_index = message[self.keys.vnf_index]
        sampling_time = message[self.keys.sampling_time]
        if vnf_id not in self.vnf_message:
            self.custom_print("first time of id, inserting in a new row",1)
            self.vnf_message[message[self.keys.vnf_id]] = message #Para que se hace esto?
        await self.cancel_all_supervisor_task()
        self.vnf_message[message[self.keys.vnf_id]] = message
        #self.delete_docker_with_name(self.get_docker_name(ns_id, vnf_id, flavor, volume))
        self.scale_process(message)
        await self.start_supervisors_in_all_vnfs()
        #self.update_ips_lb()
        """
        TODO implement this
        if flavor is not self.vnf_message[vnf_id][self.keys.flavor] and volume is not self.vnf_message[vnf_id][self.keys.volume]:
            await self.cancel_all_supervisor_task()

            self.vnf_message[message[self.keys.vnf_id]] = message
            self.delete_docker_with_name(self.get_docker_name(ns_id, vnf_id, flavor, volume))
            self.scale_process(message)
            await self.start_supervisors_in_all_vnfs()
        else: 
            print("nothing to do mantaining the scale")
            return
        """


        pending = asyncio.Task.all_tasks()  # allow end the last task!
    def delete_docker_with_name(self, docker_name):
        docker_sentence = "docker container stop  {}".format(docker_name)
        self.exec_in_os(docker_sentence)
        docker_sentence = "docker container rm  {}".format(docker_name)
        self.exec_in_os(docker_sentence)        

    def exec_in_os(self, command):
        '''Execute command in OS bash
        Parameters
        ----------
        command: str
            Command to execute.

        '''
        self.custom_print(command, 1)
        os.system(command)           
        os.system("\n")

    async def start_supervisors_in_all_vnfs(self):
        for _, vnf in self.vnf_message.items():            
            flavor = vnf[self.keys.flavor]
            volume = vnf[self.keys.volume]
            ns_id = vnf[self.keys.ns_id]
            vnf_id = vnf[self.keys.vnf_id]
            vnf_index = vnf[self.keys.vnf_index]
            sampling_time =  vnf[self.keys.sampling_time]
            supervisor =  self.build_supervisor(ns_id, vnf_id, vnf_index, sampling_time, volume, flavor)
            asyncio.ensure_future(supervisor.check_docker_loop())
        pending = asyncio.Task.all_tasks()
        self.custom_print("number of vnfs: {} current_tasks:{}".format(len(self.vnf_message),len(pending)))

    #cancel only 
    async def cancel_all_supervisor_task(self):
        self.custom_print("cancelling all supervisor task ",1)
        pending = asyncio.Task.all_tasks()
        #self.custom_print( type(pending), 1)
        cancelled = 0
        for task in pending:
            if task.cancelled():
                cancelled +=1
            if "check_docker_loop" in str(str(task)):
                self.custom_print("cancel loop", 1) 
                task.cancel()
        self.custom_print("tasks cancelled {}".format(cancelled), 1)
        
    def scale_process(self, message):
        self.custom_print(0, "scaling process")
        #get supervisors for all the dockers vs dont delete the supervisor of the other vnfs 
        flavor = message[self.keys.flavor]
        volume = message[self.keys.volume]
        ns_id = message[self.keys.ns_id]
        vnf_id = message[self.keys.vnf_id]
        vnf_index = message[self.keys.vnf_index]
        sampling_time =  message[self.keys.sampling_time]
        scale_decision = message[self.keys.scale_decision]
        if scale_decision == "scale_up":
            self.vnf_scale_module.scale_up_dockers(self.cadvisor_url, vnf_id, ns_id, volume, flavor)
        elif scale_decision == "scale_down":
            self.vnf_scale_module.scale_down_dockers(self.cadvisor_url, vnf_id, ns_id, volume, flavor)
        else:
            print ("NO DEBO HACER NADA ZZZZZZZZZZZ")
              

		
        #supervisor = DockerSupervisor(self.cadvisor_url, ns_id, vnf_id, vnf_index, sampling_time, volume, flavor)
        #supervisor.attach(self)
        #return supervisor
    def build_supervisor(self, ns_id, vnf_id, vnf_index, sampling_time, volume, flavor):
        supervisor = DockerSupervisor(self.cadvisor_url, ns_id, vnf_id, vnf_index, sampling_time, volume, flavor)
        supervisor.attach(self)
        return supervisor

    async def send_alert_to_sdm(self, message):
        url = self.sdm_ip
        self.custom_print(0,"sending alert to sdm at {}".format(url))
        async with websockets.connect(self.sdm_ip) as websocket: #todo poner esta direccion de manera no hardcodding
            await websocket.send(message)
            


    def get_ips_from(self, vnf):
        only_ips = []
        for ips in self.osm_helper.get_vnf_current_ips(vnf).values():
                for ip in ips:
                    only_ips.append(ip)
        return only_ips # returning a list of ips from a given vnf
    #todo function to send to the client to update the ips of the vnfs
    # vnf_1 : [0:ip1,1:ip2....,(n-1):ipn]
    
    #TODO get ips from the dockers made
    def update_ips_lb(self): #update ips with the new ones at the load balancer
        # used on onchange _scale up or scale down
        copyfile("./example.cfg", self.haproxy_cfg_name)

        vnf_list = self.get_current_vnfs()
        ip_list = []
        for vnf in vnf_list:
            for ip in self.get_ips_from(vnf):
                ip_list.append(ip)
        ip_list.extend(self.vnf_scale_module._get_docker_scale_ips(self.cadvisor_url))
        self.custom_print(0, "debbugging.. ips {}".format(ip_list))
        self.init_server_in_all_instances()
        self.add_ips_to_load_balancer(ip_list)
        self.copy_cfg_to_loadbalancer()
        self.restart_loadbalancer()




    def get_current_vnfs(self):
        vnf_list = []
        _, ns_vnf_dict = self.osm_helper.get_nsid_list()
        for key, ns in ns_vnf_dict.items():
            #print("type of  {}".format(type(ns["vnf"])))
            for index, vnf in ns["vnf"].items():
                #print("making debugging {} {}".format(vnf, type(vnf)))
                vnf_list.append(vnf)
        return vnf_list

    def restart_loadbalancer(self):
        restart_command = "docker restart {}".format(self.load_balancer_docker_id)
        self.custom_print(restart_command, 1)
        os.system(restart_command)

    def copy_cfg_to_loadbalancer(self):
        copy_command = "docker cp haproxy.cfg {}:/usr/local/etc/haproxy/haproxy.cfg".format(self.load_balancer_docker_id)
        self.custom_print(copy_command,1)
        os.system(copy_command)

    def add_ips_to_load_balancer(self, vnf_ips):
        with open(self.haproxy_cfg_name, "a") as myfile:
            count = 0 
            self.custom_print(vnf_ips, 1)
            for ip in vnf_ips:
                self.custom_print( ip,1)
                parsed_ips = "    server server_{} {}:{}/{}/{} \n".format(ip[-1:], ip, "8080","download","video.mp4")
                self.custom_print(parsed_ips, 1)
                myfile.write(parsed_ips)
                count += 1


    async def updateCpuUsageSubject(self, subject: CpuSubject) -> None:
        #ips  = self.osm_helper.get_vnf_current_ips(subject.vnf_id)
        #print("instances number: {}".format(len(ips[subject.vnf_id])))
        #TODO make a way to get all the instances number.
        #
        message = {
            self.keys.flavor: subject.docker_instance.flavor, 
            self.keys.volume: subject.docker_instance.volume,
            self.keys.ns_id:  subject.docker_instance.ns_id,
            self.keys.vnf_id:  subject.docker_instance.vnf_id,
            self.keys.vnf_index:  subject.docker_instance.vnf_index,
            self.keys.sampling_time: subject.docker_instance.sampling_time,
            self.keys.cpu_load: subject.cpu_load,
            self.keys.rx_usage: subject.rx_usage,
            self.keys.tx_usage: subject.tx_usage,
        }     
        print("sending message: {}".format(message))


        if((datetime.datetime.now() - self.monitoringtime).total_seconds() > 50):
            print ("An alert was risen, it will be sent as 50 sec has been elapsed")
            self.monitoringtime = datetime.datetime.now()
            await self.send_alert_to_sdm(json.dumps(message))
        else:
            print("An alert was risen, it will NOT sent as 50 sec has NOT been elapsed  ....")
        
        #self.print(self.TAG,"message sended to the sdm from docker_name: {}, cpu load: {}, ns_name: {}".format(
        #    subject.docker_name, subject.cpu_load, subject.ns_name))
        

    def get_docker_name(self, ns_id, vnf_id, flavor, volume):
        identifier =  "{}{}{}".format(flavor[0], volume[0], 0)
        return  "mn._scale_.{}.{}.{}".format(ns_id[-4:], vnf_id[-4:],identifier)

    def init_server_in_all_instances(self):
        r = requests.get(self.cadvisor_url)
        self.custom_print( self.cadvisor_url,1)
        parsed_json = r.json()
        for container in parsed_json:
            try:            
                for alias in container["aliases"]:
                    if "mn.dc"  in alias:
                        self.custom_print(alias, 1)
                        command = "docker exec -i "+alias+" nohup python3 /home/server.py > /home/server.log 2>&1&"
                        self.custom_print(command, 1)
                        os.system(command)           
                        os.system("\n")
            except KeyError as e:
                pass
                self.custom_print(0, "catch error:{}".format(e))
        del parsed_json
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arguments to work with")
    parser.add_argument('--dst_ip', default="localhost", help='destination ip')
    parser.add_argument('--sdm_ip', default="localhost",
                        help="scale decision module ip")
    parser.add_argument('--sdm_port', default="8544",
                        help="scale decision module port")
    args = parser.parse_args()
    sdm_ip = "ws://"+args.sdm_ip+":"+str(args.sdm_port)
    main_url = "https://"+args.dst_ip
    print("base_url: {}".format(main_url))
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    VnfManager(base_url=main_url, sdm_ip=sdm_ip, sdm_port = args.sdm_port)


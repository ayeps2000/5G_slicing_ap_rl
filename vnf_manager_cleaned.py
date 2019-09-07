import vnf_controller as vnf_controller
from vnf_ip_supervisor import VnfCpuSupervisor
import vnf_monitor as vnf_monitor
import requests
from yaml import load
import argparse
import urllib3
from ObserverPattern.vnf_observer_pattern import VnfObserver as Observer
from ObserverPattern.vnf_observer_pattern import VnfCpuSubject as CpuSubject
import asyncio

class VnfManager(Observer): 
    def __init__(self, base_url):
        self.start(base_url)
        

    def start(self, base_url):
        cadvisor_url  = base_url.replace("https","http") + ":8080/api/"
        base_url = base_url+":9999/osm/" #osm nbi api.
        auth_token = self.get_osm_authentication_token(base_url = base_url)
        ns_id_list, ns_vnf_list = self.get_nsid_list(base_url = base_url, auth_token = auth_token)
        loop = asyncio.get_event_loop()   
        vnf_supervisor_instances = {}
        for key, ns in ns_vnf_list.items():
            for vnf in ns["vnf"]:
                print(ns["name"])
                print(ns["vnf"][vnf])
                vnf_supervisor_instance = VnfCpuSupervisor(cadvisor_url = cadvisor_url, base_url = base_url, auth_token = auth_token, vnf_id = ns["vnf"][vnf], ns_id = key, ns_name = ns["name"], member_index = vnf)
                
                vnf_supervisor_instance.attach(self)                
                asyncio.ensure_future(vnf_supervisor_instance.check_ip_loop())
                vnf_supervisor_instances[ns["vnf"][vnf]] = vnf_supervisor_instance
            
        pending = asyncio.Task.all_tasks() #allow end the last task!
        loop.run_until_complete(asyncio.gather(*pending))
        for indx, instance in vnf_supervisor_instances.items():
            print("docker_id: {} vnf_id: {} docker_name:{}".format(instance.docker_id, instance.vnf_id, instance.docker_name))
        


    def get_osm_authentication_token(self, base_url):
        url = base_url + "admin/v1/tokens"
        payload = "------WebKitFormBoundary7MA4YWxkTrZu0gW\r\nContent-Disposition: form-data; name=\"username\"\r\n\r\nadmin\r\n------WebKitFormBoundary7MA4YWxkTrZu0gW\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\nadmin\r\n------WebKitFormBoundary7MA4YWxkTrZu0gW--"
        headers = {
            'content-type': "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW",
            'cache-control': "no-cache",
            }
        response = requests.request("POST", url, data=payload, headers=headers, verify=False)
        response_parsed = response.content.split()
        return response_parsed[2].decode("utf-8")

    def get_nsid_list(self, base_url, auth_token):
        ns_list = []
        ns_vnf_dict = {}
        url = base_url + "nslcm/v1/ns_instances"
        payload = ""
        print(url)
        headers = {
            'Content-Type': "application/",
            'Authorization': "Bearer "+ auth_token,
            'cache-control': "no-cache",
            }
        response_in_yaml = load(requests.request("GET", url, data=payload, headers=headers, verify=False).text)
        for ns in response_in_yaml:
            #print(ns["name"])
            ns_list.append(ns["id"])       
            internal_dict = {} 
            internal_dict["name"] = ns["name"]
            vnf_data = {}
            for index, vnf_list in enumerate(ns["constituent-vnfr-ref"]):
                #print(vnf_list)
                vnf_data[index] = vnf_list
            internal_dict["vnf"] = vnf_data
            ns_vnf_dict[ns["id"]] = internal_dict 
        return ns_list, ns_vnf_dict

    def updateCpuUsageSubject(self, subject: CpuSubject) -> None:
        print("reacted from docker_name: {}, cpu load: {}, ns_name: {}".format(subject.docker_name, subject.cpu_load, subject.ns_name))
    def notify_to_decision_module(self, parameter_list):

        pass
if __name__ == "__main__":    
    parser = argparse.ArgumentParser(description="Arguments to work with")
    parser.add_argument('--dst_ip',default="localhost",help='destination ip')
    args = parser.parse_args()
    main_url = "https://"+args.dst_ip
    print("base_url: {}".format(main_url))
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    VnfManager(base_url = main_url)
import docker
from adapters.orchestrators.orchestrator import OrchestratorAdapter
from models import Service, Node
from utils.dependencies_injection import inject_param
import time


class SwarmAdapter(OrchestratorAdapter):

    @inject_param('backend_adapter')
    @inject_param('logger')
    def process_event(self, event, backend_adapter=None, logger=None):
        if event['Action'] == 'update':
            logger.debug('DEBUG Event Update : %s' % event)
            if event['Type'] == 'node':
                attrs = event['Actor']['Attributes']
                if 'availability.new' in attrs:
                    if attrs['availability.new'] == 'drain':
                        self._process_node_down(attrs['name'], 'drain')
                    elif attrs['availability.new'] == 'active':
                        self._process_node_up(attrs['name'], 'active')
                elif 'state.new' in attrs:
                    if attrs['state.new'] == 'down':
                        self._process_node_down(attrs['name'], 'down')
                    elif attrs['state.new'] == 'ready':
                        self._process_node_up(attrs['name'], 'ready')
            elif event['Type'] == 'service':
                self._process_update_service(event)

    @inject_param('backend_adapter')
    @inject_param('logger')
    def _process_node_down(self, node_name, new_status, backend_adapter=None, logger=None):
        logger.info('Swarm Node %s is %s, deregister this node in backend...' % (node_name, new_status))
        backend_adapter.deregister_node(node_name)

    @inject_param('backend_adapter')
    @inject_param('logger')
    def _process_node_up(self, node_name, new_status, backend_adapter=None, logger=None):
        logger.info('Swarm Node %s is %s, process register services...' % (node_name, new_status))
        for service in self.get_services():
            backend_adapter.register_service(service)

    @inject_param('backend_adapter')
    def _process_update_service(self, event, backend_adapter=None):
        time.sleep(2)
        services = self.get_service(event)
        backend_adapter.remove_service_with_tag("swarm-service:%s" % event['Actor']['ID'])
        for service in services:
            backend_adapter.register_service(service)

    def get_services(self):
        services = []

        # If manager get swarm services
        if self._is_manager():
            swarm_services = self._get_swarm_services()

            for service in swarm_services:
                services.extend(self._get_services_object(service))

        # Get containers
        containers = self._get_containers()
        for container in containers:
            services.extend(self._get_services_object_from_container(container.id))

        return services

    def get_service(self, event):
        if event['Type'] == 'service' and self._is_manager():
            return self._get_services_object(self._get_swarm_service_by_id(event['Actor']['ID']))
        elif event['Type'] == 'container':
            return self._get_services_object_from_container(event['Actor']['ID'])

        return []

    @inject_param('logger')
    def get_service_tag_to_remove(self, event, logger=None):
        if event['Type'] == 'service' and self._is_manager():
            return 'swarm-service:%s' % event['Actor']['ID']
        elif event['Type'] == 'container' and 'com.docker.swarm.service.name' not in event['Actor']['Attributes']:
            return 'container:%s' % event['Actor']['ID']
        else:
            logger.debug("No tag to remove")
            return None

    @inject_param('logger')
    def _get_services_object(self, service, logger=None):
        exposed_ports = self._get_service_exposed_ports(service)
        services = []
        if len(exposed_ports) == 0:
            logger.info('Ignored Service : %s don\'t publish port' % service.attrs['Spec']['Name'])
        else:
            nodes = self._get_nodes_for_service(service)
            if len(nodes) == 0:
                logger.info('Ignored Service: %s don\'t run in available host' % service.attrs['Spec']['Name'])
            else:
                for port in exposed_ports:
                    tags = ['swarm-service:%s' % service.id]
                    labels, envs = self._get_swarm_service_labels_and_vars(service)
                    tags.extend(self._get_tags(labels, envs, port['internal_port']))
                    name = self._get_name_from_label_and_envs(labels, envs, port['internal_port'])
                    services.append(
                        Service(
                            name=name if name is not None else "%s-%s" % (service.attrs['Spec']['Name'], port['external_port']),
                            nodes=nodes,
                            tags=tags,
                            port=port['external_port']
                        )
                    )

            for service in services:
                logger.info("DEBUG : %s" % service.__dict__)

        return services

    @inject_param('logger')
    def _get_services_object_from_container(self, container_id, logger=None):
        container = self._get_container_from_id(container_id)
        waiting = 0
        # Wait if container is not running
        while container.attrs['State']['Status'] != "running" and waiting != 10:
            logger.debug("DEBUG container status : %s wait..." % container.attrs['State']['Status'])
            time.sleep(1)
            waiting += 1
            container = self._get_container_from_id(container_id)

        exposed_ports = self._get_container_exposed_ports(container)
        container_name = container.attrs['Config']['Labels']['com.docker.compose.service'] if 'com.docker.compose.service' in container.attrs['Config']['Labels'] else container.attrs['Name'].replace('/', '')
        services = []
        if len(exposed_ports) == 0:
            logger.info('Ignored Service : %s don\'t publish port' % container_name)
        else:
            for port in exposed_ports:
                tags = ['container:%s' % container.id]
                labels, envs = self._get_container_labels_and_vars(container)
                tags.extend(self._get_tags(labels, envs, port['internal_port']))
                name = self._get_name_from_label_and_envs(labels, envs, port['internal_port'])
                services.append(
                    Service(
                        name=name if name is not None else "%s-%s" % (container_name, port['internal_port']),
                        port=port['external_port'],
                        nodes=[
                            Node(
                                name=self._get_local_node_name(),
                                address=self._get_local_node_address()
                            )
                        ],
                        tags=tags
                    )
                )

        return services

    def _get_nodes_for_service(self, swarm_service):
        result = []

        nodes = [node.attrs for node in self._list_nodes()]
        for node_attrs in nodes:
            if node_attrs['Status']['State'] == 'ready':
                result.append(Node(name=node_attrs['Description']['Hostname'], address=node_attrs['Status']['Addr']))

        return result

    @inject_param('logger')
    def _get_service_exposed_ports(self, swarm_service, logger=None):
        if 'Ports' in swarm_service.attrs['Endpoint']:
            logger.debug('DEBUG : %s' % swarm_service.attrs['Endpoint']['Ports'])

        return [
            {"external_port": port['PublishedPort'], "internal_port": port['TargetPort']}
            for port in swarm_service.attrs['Endpoint']['Ports']
            if 'PublishedPort' in port
        ] if 'Ports' in swarm_service.attrs['Endpoint'] else []

    def _get_containers(self):
        return [
            container
            for container in self._list_container()
            if container.status == 'running' and 'com.docker.swarm.service.id' not in container.attrs['Config']['Labels']
        ]

    @inject_param('logger')
    def _get_container_exposed_ports(self, container, logger=None):
        ports = []
        logger.debug("DEBUG NetworkSetting : %s" % container.attrs['NetworkSettings']['Ports'])
        for key in container.attrs['NetworkSettings']['Ports'].keys():
            if container.attrs['NetworkSettings']['Ports'][key] is not None and len(container.attrs['NetworkSettings']['Ports'][key][0]['HostPort']) != 0:
                ports.append({
                    'internal_port': int(key.replace('/tcp', '').replace('/udp', '')),
                    'external_port': int(container.attrs['NetworkSettings']['Ports'][key][0]['HostPort'])
                })

        logger.debug('DEBUG ports : %s' % ports)
        return ports

    @inject_param('logger')
    def _get_swarm_service_labels_and_vars(self, swarm_service, logger=None):
        labels = swarm_service.attrs['Spec']['Labels']
        logger.debug("DEBUG labels : %s" % labels)
        envs = [
            env
            for env in swarm_service.attrs['Spec']['TaskTemplate']['ContainerSpec']['Env']
        ] if 'Env' in swarm_service.attrs['Spec']['TaskTemplate']['ContainerSpec'] else []

        return labels, envs

    @inject_param('logger')
    def _get_container_labels_and_vars(self, container, logger=None):
        labels = container.attrs['Config']['Labels']
        logger.debug("DEBUG labels : %s" % labels)
        envs = [
            env
            for env in container.attrs['Config']['Env']
        ] if 'Env' in container.attrs['Config'] else []

        return labels, envs

    @inject_param('logger')
    def _get_tags(self, labels, envs, internal_port, logger=None):
        tags = []
        keys = ["service_tags", "service_%s_tags" % internal_port]
        envs_dict = {}
        for env in envs:
            envs_dict[env.split('=')[0].lower()] = env.split('=')[1]
        logger.debug("DEBUG envs : %s" % envs_dict)

        for key in keys:
            if key in labels:
                tags.extend(labels[key].split(','))
            else:
                logger.debug("DEBUG : key %s not in %s" % (key, labels))

            if key in envs_dict:
                tags.extend(envs_dict[key].split(','))
            else:
                logger.debug("DEBUG : key %s not in %s" % (key, envs_dict))

        logger.debug("DEBUG Tags : %s" % list(set(tags)))

        return list(set(tags))

    @inject_param('logger')
    def _get_name_from_label_and_envs(self, labels, envs, internal_port, logger=None):
        keys = ['service_name', 'service_%s_name' % internal_port]
        envs_dict = {}
        for env in envs:
            envs_dict[env.split('=')[0].lower()] = env.split('=')[1]
        logger.debug("DEBUG envs : %s" % envs_dict)

        for key in keys:
            if key in labels:
                return labels[key]

            if key in envs_dict:
                return envs_dict[key]

        return None

    def _get_local_node_name(self):
        client = self._get_docker_socket()
        return client.info()['Name']

    def _get_local_node_address(self):
        client = self._get_docker_socket()
        return client.info()['Swarm']['NodeAddr']

    def _list_nodes(self):
        client = self._get_docker_socket()
        return client.nodes.list()

    def _list_container(self):
        client = self._get_docker_socket()
        return client.containers.list()

    def _get_swarm_service_by_id(self, service_id):
        client = self._get_docker_socket()
        return client.services.get(service_id)

    def _get_container_from_id(self, container_id):
        client = self._get_docker_socket()
        return client.containers.get(container_id)

    def _is_manager(self):
        client = self._get_docker_socket()
        return client.info()['Swarm']['ControlAvailable']

    def _get_swarm_services(self):
        client = self._get_docker_socket()
        return client.services.list()

    def _get_docker_socket(self):
        return docker.DockerClient(base_url='unix://var/run/docker.sock', version='auto')

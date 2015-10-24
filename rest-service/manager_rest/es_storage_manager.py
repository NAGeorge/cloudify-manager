#########
# Copyright (c) 2013 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.


import elasticsearch.exceptions
from elasticsearch import Elasticsearch
from flask import current_app
from flask_securest import rest_security

from manager_rest import config
from manager_rest import manager_exceptions
from manager_rest.models import (BlueprintState,
                                 Deployment,
                                 DeploymentModification,
                                 Execution,
                                 DeploymentNode,
                                 DeploymentNodeInstance,
                                 ProviderContext,
                                 Plugin)

STORAGE_INDEX_NAME = 'cloudify_storage'
NODE_TYPE = 'node'
NODE_INSTANCE_TYPE = 'node_instance'
PLUGIN_TYPE = 'plugin'
BLUEPRINT_TYPE = 'blueprint'
DEPLOYMENT_TYPE = 'deployment'
DEPLOYMENT_MODIFICATION_TYPE = 'deployment_modification'
EXECUTION_TYPE = 'execution'
PROVIDER_CONTEXT_TYPE = 'provider_context'
PROVIDER_CONTEXT_ID = 'CONTEXT'

DEFAULT_SEARCH_SIZE = 10000

MUTATE_PARAMS = {
    'refresh': True
}


class ESStorageManager(object):

    def __init__(self, host, port, security_enabled):
        self.es_host = 'localhost'
        self.es_port = '9200'
        self.security_enabled = True
        # self.es_host = host
        # self.es_port = port
        # self.security_enabled = security_enabled

    @property
    def _connection(self):
        return Elasticsearch(hosts=[{'host': self.es_host,
                                     'port': self.es_port}])

    def _list_docs(self, doc_type, model_class, query=None, fields=None):
        include = list(fields) if fields else True
        search_result = self._connection.search(index=STORAGE_INDEX_NAME,
                                                doc_type=doc_type,
                                                size=DEFAULT_SEARCH_SIZE,
                                                body=query,
                                                _source=include)
        docs = map(lambda hit: hit['_source'], search_result['hits']['hits'])

        # ES doesn't return _version if using its search API.
        if doc_type == NODE_INSTANCE_TYPE:
            for doc in docs:
                doc['version'] = None
        return [self._fill_missing_fields_and_deserialize(doc, model_class)
                for doc in docs]

    def _get_doc(self, doc_type, doc_id, fields=None):
        try:
            query = self._build_filter_terms_and_acl_query(
                required_permission='GET',
                filters={'_id': doc_id})
            if fields:
                results = self._connection.search(index=STORAGE_INDEX_NAME,
                                                  doc_type=doc_type,
                                                  body=query,
                                                  _source=[f for f in fields])
                # return self._connection.get(index=STORAGE_INDEX_NAME,
                #                             doc_type=doc_type,
                #                             id=doc_id,
                #                             _source=[f for f in fields])
            else:
                results = self._connection.search(index=STORAGE_INDEX_NAME,
                                                  doc_type=doc_type,
                                                  body=query)
                # return self._connection.get(index=STORAGE_INDEX_NAME,
                #                             doc_type=doc_type,
                #                             id=doc_id)
            results_hits = results.get('hits', {})
            results_hits_total = results_hits.get('total', 0)
            if results_hits_total == 0:
                current_app.logger.error('***** results not found!')
                raise manager_exceptions.NotFoundError(
                    '{0} {1} not found'.format(doc_type, doc_id))
            if results_hits_total > 1:
                current_app.logger.error('***** too many documents matched'
                                         ' the search!')
                raise manager_exceptions.NotFoundError(
                    'more than one document of {0} {1} found'.
                    format(doc_type, doc_id))
            return results_hits.get('hits')[0]
        except elasticsearch.exceptions.NotFoundError:
            raise manager_exceptions.NotFoundError(
                '{0} {1} not found'.format(doc_type, doc_id))

    def _get_doc_and_deserialize(self, doc_type, doc_id, model_class,
                                 fields=None):
        doc = self._get_doc(doc_type, doc_id, fields)

        if not doc:
            current_app.logger.error('***** no docs found!')
        else:
            current_app.logger.info('***** got doc of type {0} and id {1}'
                                    .format(doc_type, doc_id))

        if not fields:
            return model_class(**doc['_source'])
        else:
            if len(fields) != len(doc['_source']):
                missing_fields = [field for field in fields if field not
                                  in doc['_source']]
                raise RuntimeError('Some or all fields specified for query '
                                   'were missing: {0}'.format(missing_fields))
            fields_data = doc['_source']
            return self._fill_missing_fields_and_deserialize(fields_data,
                                                             model_class)

    def _put_doc_if_not_exists(self, doc_type, doc_id, value):
        try:
            current_app.logger.info('***** starting _put_doc_if_not_exists')
            current_app.logger.info('***** STORAGE_INDEX_NAME: {0}'.
                                    format(STORAGE_INDEX_NAME))
            current_app.logger.info('***** doc_type: {0}'.format(doc_type))
            current_app.logger.info('***** doc_id: {0}'.format(doc_id))
            current_app.logger.info('***** body: {0}'.format(value))
            current_app.logger.info('***** MUTATE_PARAMS: {0}'.
                                    format(MUTATE_PARAMS))
            self._connection.create(index=STORAGE_INDEX_NAME,
                                    doc_type=doc_type, id=doc_id,
                                    body=value,
                                    **MUTATE_PARAMS)
        except elasticsearch.exceptions.ConflictError:
            raise manager_exceptions.ConflictError(
                '{0} {1} already exists'.format(doc_type, doc_id))
        except Exception as e:
            raise manager_exceptions.GeneralError(
                'Failed to create document {0} in {1}, error: {2}'.
                format(doc_id, doc_type, e.message))

    def _delete_doc(self, doc_type, doc_id, model_class, id_field='id'):
        try:
            res = self._connection.delete(STORAGE_INDEX_NAME, doc_type,
                                          doc_id,
                                          **MUTATE_PARAMS)
        except elasticsearch.exceptions.NotFoundError:
            raise manager_exceptions.NotFoundError(
                "{0} {1} not found".format(doc_type, doc_id))

        fields_data = {
            id_field: res['_id']
        }
        return self._fill_missing_fields_and_deserialize(fields_data,
                                                         model_class)

    def _delete_doc_by_query(self, doc_type, query):
        self._connection.delete_by_query(index=STORAGE_INDEX_NAME,
                                         doc_type=doc_type,
                                         body=query)

    @staticmethod
    def _fill_missing_fields_and_deserialize(fields_data, model_class):
        for field in model_class.fields:
            if field not in fields_data:
                fields_data[field] = None
        return model_class(**fields_data)

    def _build_filter_terms_and_acl_query(self,
                                          required_permission=None,
                                          filters=None):
        """
        This method is used to create a search filter to receive only results
        where a specific key holds a specific value.
        Filters are faster than queries as they are cached and don't
        influence the score.
        :param filters: a dictionary containing filters keys and their expected
         value
        :return: an elasticsearch query string containing the given filters
        """
        filters_terms = []
        acl_terms = []
        query_as_dict = None
        sub_queries = []

        if self.security_enabled:
            principals_list = rest_security.get_principals_list()
            current_app.logger.info('***** principals list: {0}'.
                                    format(principals_list))
            acceptable_aces = ESStorageManager._calc_acceptable_aces(
                required_permission, principals_list)
            current_app.logger.info('***** acceptable_aces: {0}'.
                                    format(acceptable_aces))
            for ace in acceptable_aces:
                acl_terms.append({'wildcard': {'acl': '*{0}*'.format(ace)}})

        if filters:
            for key, val in filters.iteritems():
                filters_terms.append({'term': {key: val}})

        if filters_terms:
            filter_query = {
                'filter': {
                    'bool': {
                        'must': filters_terms
                    }
                }
            }
            sub_queries.append(filter_query)

        if acl_terms:
            acl_query = {
                'query': {
                    'bool': {
                        'should': [
                            acl_terms
                        ]
                    }
                }
            }
            sub_queries.append(acl_query)

        if len(sub_queries) > 0:
            query_as_dict = {
                'query': {
                    'filtered': {
                    }
                }
            }
            for sub_query in sub_queries:
                query_as_dict['query']['filtered'].update(sub_query)

        current_app.logger.info('***** built query: {0}'.
                                format(query_as_dict))
        return query_as_dict

    # todo(adaml): who uses this?
    def node_instances_list(self, include=None):
        current_app.logger.info('***** started node_instances_list')
        search_result = self._connection.search(index=STORAGE_INDEX_NAME,
                                                doc_type=NODE_INSTANCE_TYPE,
                                                size=DEFAULT_SEARCH_SIZE,
                                                _source=include or True)
        docs_with_versions = \
            map(lambda hit: (hit['_source'], hit['_version']),
                search_result['hits']['hits'])
        result = map(
            lambda doc_with_version: DeploymentNodeInstance(
                version=doc_with_version[1], **doc_with_version[0]),
            docs_with_versions)
        current_app.logger.info('***** ended node_instances_list')
        return result

    def blueprints_list(self, include=None, filters=None):
        current_app.logger.info('***** ended blueprints_list')
        result = self._get_items_list(BLUEPRINT_TYPE,
                                      BlueprintState,
                                      filters=filters,
                                      include=include)
        current_app.logger.info('***** ended blueprints_list')
        return result

    def deployments_list(self, include=None, filters=None):
        current_app.logger.info('***** starting deployments_list')
        result = self._get_items_list(DEPLOYMENT_TYPE,
                                      Deployment,
                                      filters=filters,
                                      include=include)
        current_app.logger.info('***** ended deployments_list')
        return result

    def executions_list(self, include=None, filters=None):
        current_app.logger.info('***** starting executions_list')
        result = self._get_items_list(EXECUTION_TYPE,
                                      Execution,
                                      filters=filters,
                                      include=include)
        current_app.logger.info('***** ended executions_list')
        return result

    def get_blueprint_deployments(self, blueprint_id, include=None):
        current_app.logger.info('***** starting get_blueprint_deployments')
        deployment_filters = {'blueprint_id': blueprint_id}
        result = self._get_items_list(DEPLOYMENT_TYPE,
                                      Deployment,
                                      filters=deployment_filters,
                                      include=include)
        current_app.logger.info('***** ended get_blueprint_deployments')
        return result

    def get_node_instance(self, node_instance_id, include=None):
        current_app.logger.info('***** starting get_node_instance')
        doc = self._get_doc(NODE_INSTANCE_TYPE,
                            node_instance_id,
                            fields=include)
        node = DeploymentNodeInstance(version=doc['_version'],
                                      **doc['_source'])
        current_app.logger.info('***** ended get_node_instance')
        return node

    def get_node(self, deployment_id, node_id, include=None):
        current_app.logger.info('***** starting get_node')
        storage_node_id = self._storage_node_id(deployment_id, node_id)
        result = self._get_doc_and_deserialize(doc_id=storage_node_id,
                                               doc_type=NODE_TYPE,
                                               model_class=DeploymentNode,
                                               fields=include)
        current_app.logger.info('***** ended get_node')
        return result

    def get_node_instances(self, include=None, filters=None):
        current_app.logger.info('***** starting get_node_instances')
        result = self._get_items_list(NODE_INSTANCE_TYPE,
                                      DeploymentNodeInstance,
                                      filters=filters,
                                      include=include)
        current_app.logger.info('***** ended get_node_instances')
        return result

    def get_plugins(self, include=None, filters=None):
        return self._get_items_list(PLUGIN_TYPE,
                                    Plugin,
                                    filters=filters,
                                    include=include)

    def get_nodes(self, include=None, filters=None):
        current_app.logger.info('***** starting get_nodes')
        import traceback
        with open('/tmp/manager_log.tmp', 'a') as logfile:
            traceback.print_stack(file=logfile)
        result = self._get_items_list(NODE_TYPE,
                                      DeploymentNode,
                                      filters=filters,
                                      include=include)
        current_app.logger.info('***** ended get_nodes')
        return result

    def _get_items_list(self, doc_type, model_class, filters=None,
                        include=None):
        query = self._build_filter_terms_and_acl_query(
            required_permission='GET',
            filters=filters)
        return self._list_docs(doc_type,
                               model_class,
                               query=query,
                               fields=include)

    def get_blueprint(self, blueprint_id, include=None):
        current_app.logger.info('***** starting get_blueprint')
        result = self._get_doc_and_deserialize(BLUEPRINT_TYPE,
                                               blueprint_id,
                                               BlueprintState,
                                               fields=include)
        current_app.logger.info('***** ended get_blueprint')
        return result

    def get_deployment(self, deployment_id, include=None):
        current_app.logger.info('***** starting get_deployment')
        result = self._get_doc_and_deserialize(DEPLOYMENT_TYPE,
                                               deployment_id,
                                               Deployment,
                                               fields=include)
        current_app.logger.info('***** ended get_deployment')
        return result

    def get_execution(self, execution_id, include=None):
        current_app.logger.info('***** starting get_execution')
        result = self._get_doc_and_deserialize(EXECUTION_TYPE,
                                               execution_id,
                                               Execution,
                                               fields=include)
        current_app.logger.info('***** ended get_execution')
        return result

    def get_plugin(self, plugin_id, include=None):
        return self._get_doc_and_deserialize(PLUGIN_TYPE,
                                             plugin_id,
                                             Plugin,
                                             fields=include)

    def put_blueprint(self, blueprint_id, blueprint):
        current_app.logger.info('***** starting put_blueprint')
        self._put_doc_if_not_exists(BLUEPRINT_TYPE, str(blueprint_id),
                                    blueprint.to_dict())
        current_app.logger.info('***** ended put_blueprint')

    def put_deployment(self, deployment_id, deployment):
        current_app.logger.info('***** starting put_deployment')
        self._put_doc_if_not_exists(DEPLOYMENT_TYPE, str(deployment_id),
                                    deployment.to_dict())
        current_app.logger.info('***** ended put_deployment')

    def put_execution(self, execution_id, execution):
        current_app.logger.info('***** starting put_execution')
        self._put_doc_if_not_exists(EXECUTION_TYPE, str(execution_id),
                                    execution.to_dict())
        current_app.logger.info('***** ended put_execution')

    def put_plugin(self, plugin):
        self._put_doc_if_not_exists(PLUGIN_TYPE, str(plugin.id),
                                    plugin.to_dict())

    def put_node(self, node):
        current_app.logger.info('***** starting put_node')
        storage_node_id = self._storage_node_id(node.deployment_id, node.id)
        doc_data = node.to_dict()
        self._put_doc_if_not_exists(NODE_TYPE, storage_node_id, doc_data)
        current_app.logger.info('***** ended put_node')

    def put_node_instance(self, node_instance):
        current_app.logger.info('***** starting put_node_instance')
        node_instance_id = node_instance.id
        doc_data = node_instance.to_dict()
        del(doc_data['version'])
        self._put_doc_if_not_exists(NODE_INSTANCE_TYPE,
                                    str(node_instance_id),
                                    doc_data)
        current_app.logger.info('***** ended put_node_instance')
        return 1

    def delete_blueprint(self, blueprint_id):
        current_app.logger.info('***** starting delete_blueprint')
        result = self._delete_doc(BLUEPRINT_TYPE, blueprint_id,
                                  BlueprintState)
        current_app.logger.info('***** ended delete_blueprint')
        return result

    def delete_plugin(self, plugin_id):
        return self._delete_doc(PLUGIN_TYPE, plugin_id, Plugin)

    def update_execution_status(self, execution_id, status, error):
        current_app.logger.info('***** starting update_execution_status')
        update_doc_data = {'status': status,
                           'error': error}
        update_doc = {'doc': update_doc_data}

        try:
            self._connection.update(index=STORAGE_INDEX_NAME,
                                    doc_type=EXECUTION_TYPE,
                                    id=str(execution_id),
                                    body=update_doc,
                                    **MUTATE_PARAMS)
        except elasticsearch.exceptions.NotFoundError:
            raise manager_exceptions.NotFoundError(
                "Execution {0} not found".format(execution_id))
        current_app.logger.info('***** ended update_execution_status')

    def update_provider_context(self, provider_context):
        current_app.logger.info('***** starting update_provider_context')
        doc_data = {'doc': provider_context.to_dict()}
        try:
            self._connection.update(index=STORAGE_INDEX_NAME,
                                    doc_type=PROVIDER_CONTEXT_TYPE,
                                    id=PROVIDER_CONTEXT_ID,
                                    body=doc_data,
                                    **MUTATE_PARAMS)
        except elasticsearch.exceptions.NotFoundError:
            raise manager_exceptions.NotFoundError(
                'Provider Context not found')
        current_app.logger.info('***** ended update_provider_context')

    def delete_deployment(self, deployment_id):
        current_app.logger.info('***** starting delete_deployment')
        query = self._build_filter_terms_and_acl_query(
            required_permission='DELETE',
            filters={'deployment_id': deployment_id})
        self._delete_doc_by_query(EXECUTION_TYPE, query)
        self._delete_doc_by_query(NODE_INSTANCE_TYPE, query)
        self._delete_doc_by_query(NODE_TYPE, query)
        self._delete_doc_by_query(DEPLOYMENT_MODIFICATION_TYPE, query)
        result = self._delete_doc(DEPLOYMENT_TYPE, deployment_id, Deployment)
        current_app.logger.info('***** ended delete_deployment')
        return result

    def delete_execution(self, execution_id):
        current_app.logger.info('***** starting delete_execution')
        result = self._delete_doc(EXECUTION_TYPE, execution_id, Execution)
        current_app.logger.info('***** ended delete_execution')
        return result

    def delete_node(self, node_id):
        current_app.logger.info('***** starting delete_node')
        result = self._delete_doc(NODE_TYPE, node_id, DeploymentNode)
        current_app.logger.info('***** ended delete_node')
        return result

    def delete_node_instance(self, node_instance_id):
        current_app.logger.info('***** starting delete_node_instance')
        result = self._delete_doc(NODE_INSTANCE_TYPE,
                                  node_instance_id,
                                  DeploymentNodeInstance)
        current_app.logger.info('***** ended delete_node_instance')
        return result

    def update_node(self, deployment_id, node_id,
                    number_of_instances=None,
                    planned_number_of_instances=None):
        current_app.logger.info('***** starting update_node')
        storage_node_id = self._storage_node_id(deployment_id, node_id)
        update_doc_data = {}
        if number_of_instances is not None:
            update_doc_data['number_of_instances'] = number_of_instances
        if planned_number_of_instances is not None:
            update_doc_data[
                'planned_number_of_instances'] = planned_number_of_instances
        update_doc = {'doc': update_doc_data}
        try:
            self._connection.update(index=STORAGE_INDEX_NAME,
                                    doc_type=NODE_TYPE,
                                    id=storage_node_id,
                                    body=update_doc,
                                    **MUTATE_PARAMS)
        except elasticsearch.exceptions.NotFoundError:
            raise manager_exceptions.NotFoundError(
                "Node {0} not found".format(node_id))
        current_app.logger.info('***** ended update_node')

    def update_node_instance(self, node):
        current_app.logger.info('***** starting update_node_instance')
        new_state = node.state
        new_runtime_props = node.runtime_properties
        new_relationships = node.relationships

        current = self.get_node_instance(node.id)
        # Validate version - this is not 100% safe since elasticsearch
        # update doesn't accept the version field.
        if node.version != 0 and current.version != node.version:
            raise manager_exceptions.ConflictError(
                'Node instance update conflict [current_version={0}, updated_'
                'version={1}]'.format(current.version, node.version))

        if new_state is not None:
            current.state = new_state

        if new_runtime_props is not None:
            current.runtime_properties = new_runtime_props

        if new_relationships is not None:
            current.relationships = new_relationships

        updated = current.to_dict()
        del updated['version']

        self._connection.index(index=STORAGE_INDEX_NAME,
                               doc_type=NODE_INSTANCE_TYPE,
                               id=node.id,
                               body=updated,
                               **MUTATE_PARAMS)
        current_app.logger.info('***** ended update_node_instance')

    def put_provider_context(self, provider_context):
        current_app.logger.info('***** starting put_provider_context')
        doc_data = provider_context.to_dict()
        current_app.logger.info('***** in put_provider_context, putting: {0}'.
                                format(doc_data))
        self._put_doc_if_not_exists(PROVIDER_CONTEXT_TYPE,
                                    PROVIDER_CONTEXT_ID,
                                    doc_data)
        current_app.logger.info('***** ended put_provider_context')

    def get_provider_context(self, include=None):
        current_app.logger.info('***** starting get_provider_context')
        result = self._get_doc_and_deserialize(PROVIDER_CONTEXT_TYPE,
                                               PROVIDER_CONTEXT_ID,
                                               ProviderContext,
                                               fields=include)
        current_app.logger.info('***** ended get_provider_context')
        return result

    def put_deployment_modification(self, modification_id, modification):
        current_app.logger.info('***** starting put_deployment_modification')
        self._put_doc_if_not_exists(DEPLOYMENT_MODIFICATION_TYPE,
                                    modification_id,
                                    modification.to_dict())
        current_app.logger.info('***** ended put_deployment_modification')

    def get_deployment_modification(self, modification_id, include=None):
        current_app.logger.info('***** starting get_deployment_modification')
        result = self._get_doc_and_deserialize(DEPLOYMENT_MODIFICATION_TYPE,
                                               modification_id,
                                               DeploymentModification,
                                               fields=include)
        current_app.logger.info('***** ended get_deployment_modification')
        return result

    def update_deployment_modification(self, modification):
        current_app.logger.info('***** starting '
                                'update_deployment_modification')
        modification_id = modification.id
        update_doc_data = {}
        if modification.status is not None:
            update_doc_data['status'] = modification.status
        if modification.ended_at is not None:
            update_doc_data['ended_at'] = modification.ended_at
        if modification.node_instances is not None:
            update_doc_data['node_instances'] = modification.node_instances

        update_doc = {'doc': update_doc_data}
        try:
            self._connection.update(index=STORAGE_INDEX_NAME,
                                    doc_type=DEPLOYMENT_MODIFICATION_TYPE,
                                    id=modification_id,
                                    body=update_doc,
                                    **MUTATE_PARAMS)
        except elasticsearch.exceptions.NotFoundError:
            raise manager_exceptions.NotFoundError(
                "Modification {0} not found".format(modification_id))
        current_app.logger.info('***** ended update_deployment_modification')

    def deployment_modifications_list(self, include=None, filters=None):
        current_app.logger.info('***** starting deployment_modifications_list')
        result = self._get_items_list(DEPLOYMENT_MODIFICATION_TYPE,
                                      DeploymentModification,
                                      filters=filters,
                                      include=include)
        current_app.logger.info('***** ended deployment_modifications_list')
        return result

    @staticmethod
    def _storage_node_id(deployment_id, node_id):
        return '{0}_{1}'.format(deployment_id, node_id)

    @staticmethod
    def _calc_acceptable_aces(required_permission, principals_list):
        all_have_all_permissions = 'ALLOW#ALL#ALL'
        all_have_required_permission = 'ALLOW#ALL#{0}'.\
            format(required_permission)
        acceptable_aces = [all_have_all_permissions,
                           all_have_required_permission]
        if principals_list:
            for principal in principals_list:
                principal_has_all_permissions = 'ALLOW#{0}#ALL'.\
                    format(principal)
                principal_has_required_permission = 'ALLOW#{0}#{1}'.\
                    format(principal, required_permission)
                acceptable_aces.append(principal_has_all_permissions)
                acceptable_aces.append(principal_has_required_permission)

        return acceptable_aces


def create():
    configuration = config.instance()
    return ESStorageManager(
        configuration.db_address,
        configuration.db_port,
        configuration.security_enabled
    )

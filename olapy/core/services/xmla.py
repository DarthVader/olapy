# -*- encoding: utf8 -*-

from __future__ import absolute_import, division, print_function

import xmlwitch
import os
import sys
import logging
import imp
from datetime import datetime
from os.path import expanduser

from spyne import AnyXml, Application, ServiceBase, rpc, Fault
from spyne.const.http import HTTP_200
from spyne.error import InvalidCredentialsError
from spyne.protocol.soap import Soap11
from spyne.server.http import HttpTransportContext
from spyne.server.wsgi import WsgiApplication

from ..mdx.tools.config_file_parser import ConfigParser
from ..services.models import DiscoverRequest
from ..services.models import ExecuteRequest, Session
from .xmla_discover_tools import XmlaDiscoverTools
from .xmla_execute_tools import XmlaExecuteTools
from .xmla_execute_xsds import execute_xsd


class XmlaSoap11(Soap11):
    def create_in_document(self, ctx, charset=None):
        if isinstance(ctx.transport, HttpTransportContext):
            http_verb = ctx.transport.get_request_method()
            if http_verb == "OPTIONS":
                ctx.transport.resp_headers['allow'] = "POST, OPTIONS"
                ctx.transport.respond(HTTP_200)
                raise Fault("")
        return Soap11.create_in_document(self, ctx, charset)


class XmlaProviderService(ServiceBase):
    """
    The main class to active soap services between xmla clients and olapy.

    IMPORTANT : all xsd and soap responses are written manually (not generated by Spyne lib)
    because Spyne doesn't support encodingStyle and other namespaces required by Excel,
    check it <http://stackoverflow.com/questions/25046837/the-encodingstyle-attribute-is-not-allowed-in-spyne>

    we have to instantiate XmlaDiscoverTools and declare variables
    as class variable so we can access them in Discovery and Execute functions
    this problem is related with Spyne architecture, NO CHOICE

    NOTE : some variables and functions names shouldn't respect naming convention here
    because we need to create the xmla response (generated by spyne) with the same variable names,
    and then, xmla requests from excel can be reached
    thus make life easier.
    """

    discover_tools = XmlaDiscoverTools()
    sessio_id = discover_tools.session_id

    @rpc(DiscoverRequest,
         _returns=AnyXml,
         _body_style="bare",
         _out_header=Session,
         _throws=InvalidCredentialsError)
    def Discover(ctx, request):
        """
        The first principle function of xmla protocol.

        :param request: Discover function must take 3 argument ( JUST 3 ! ) RequestType,
            Restrictions and Properties , we encapsulate them in DiscoverRequest object

        :return: Discover response in xmla format

        """
        # ctx is the 'context' parameter used by Spyne
        # (which cause problems when we want to access xmla_provider instantiation variables)
        discover_tools = XmlaProviderService.discover_tools
        ctx.out_header = Session(SessionId=str(XmlaProviderService.sessio_id))

        config_parser = ConfigParser(discover_tools.executer.cube_path)
        if config_parser.xmla_authentication() and ctx.transport.req_env[
                'QUERY_STRING'] != 'admin':

            raise InvalidCredentialsError(
                fault_string='You do not have permission to access this resource',
                fault_object=None)
            # TODO call (labster) login function or create login with token (according to labster db)

        if request.RequestType == "DISCOVER_DATASOURCES":
            return discover_tools.discover_datasources_response()

        elif request.RequestType == "DISCOVER_PROPERTIES":
            return discover_tools.discover_properties_response(request)

        elif request.RequestType == "DISCOVER_SCHEMA_ROWSETS":
            return discover_tools.discover_schema_rowsets_response(request)

        elif request.RequestType == "DISCOVER_LITERALS":
            return discover_tools.discover_literals_response(request)

        elif request.RequestType == "MDSCHEMA_SETS":
            return discover_tools.discover_mdschema_sets_response(request)

        elif request.RequestType == "MDSCHEMA_KPIS":
            return discover_tools.discover_mdschema_kpis_response(request)

        elif request.RequestType == "DBSCHEMA_CATALOGS":
            return discover_tools.discover_dbschema_catalogs_response(request)

        elif request.RequestType == "MDSCHEMA_CUBES":
            return discover_tools.discover_mdschema_cubes_response(request)

        elif request.RequestType == "DBSCHEMA_TABLES":
            return discover_tools.discover_dbschema_tables_response(request)

        elif request.RequestType == "MDSCHEMA_MEASURES":
            return discover_tools.discover_mdschema_measures__response(request)

        elif request.RequestType == "MDSCHEMA_DIMENSIONS":
            return discover_tools.discover_mdschema_dimensions_response(
                request)

        elif request.RequestType == "MDSCHEMA_HIERARCHIES":
            return discover_tools.discover_mdschema_hierarchies_response(
                request)

        # todo back here !!
        elif request.RequestType == "MDSCHEMA_LEVELS":
            return discover_tools.discover_mdschema_levels__response(request)

        elif request.RequestType == "MDSCHEMA_MEASUREGROUPS":
            return discover_tools.discover_mdschema_measuresgroups_response(
                request)

        elif request.RequestType == "MDSCHEMA_MEASUREGROUP_DIMENSIONS":
            return discover_tools.discover_mdschema_measuresgroups_dimensions_response(
                request)

        elif request.RequestType == "MDSCHEMA_PROPERTIES":
            return discover_tools.discover_mdschema_properties_response(
                request)

        elif request.RequestType == "MDSCHEMA_MEMBERS":
            return discover_tools.discover_mdschema_members_response(request)

    # Execute function must take 2 argument ( JUST 2 ! ) Command and Properties
    # we encapsulate them in ExecuteRequest object
    @rpc(ExecuteRequest,
         _returns=AnyXml,
         _body_style="bare",
         _out_header=Session)
    def Execute(ctx, request):
        """
        The second principle function of xmla protocol.

        :param request: Execute function must take 2 argument ( JUST 2 ! ) Command and Properties,
            we encapsulate them in ExecuteRequest object

        :return: Execute response in xml format
        """
        ctx.out_header = Session(SessionId=str(XmlaProviderService.sessio_id))
        if request.Command.Statement == '':
            # check if command contains a query

            xml = xmlwitch.Builder()
            with xml['return']:
                xml.root(xmlns="urn:schemas-microsoft-com:xml-analysis:empty")

            return str(xml)

        else:

            XmlaProviderService.discover_tools.change_catalogue(
                request.Properties.PropertyList.Catalog)

            xml = xmlwitch.Builder()
            executer = XmlaProviderService.discover_tools.executer
            executer.mdx_query = request.Command.Statement

            # todo Hierarchize
            if all(key in request.Command.Statement
                   for key in
                   ['WITH MEMBER', 'strtomember', '[Measures].[XL_SD0]']):

                convert2formulas = True
            else:
                convert2formulas = False

            xmla_tools = XmlaExecuteTools(executer, convert2formulas)

            with xml['return']:
                with xml.root(
                        xmlns="urn:schemas-microsoft-com:xml-analysis:mddataset",
                        **{
                            'xmlns:xsd': 'http://www.w3.org/2001/XMLSchema',
                            'xmlns:xsi':
                            'http://www.w3.org/2001/XMLSchema-instance'
                        }):
                    xml.write(execute_xsd)
                    with xml.OlapInfo:
                        xml.write(xmla_tools.generate_cell_info())
                        with xml.CubeInfo:
                            with xml.Cube:
                                xml.CubeName('Sales')
                                xml.LastDataUpdate(
                                    datetime.now().strftime(
                                        '%Y-%m-%dT%H:%M:%S'),
                                    xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"
                                )
                                xml.LastSchemaUpdate(
                                    datetime.now().strftime(
                                        '%Y-%m-%dT%H:%M:%S'),
                                    xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"
                                )

                        with xml.AxesInfo:
                            xml.write(xmla_tools.generate_axes_info())
                            xml.write(xmla_tools.generate_axes_info_slicer())

                    with xml.Axes:
                        xml.write(xmla_tools.generate_xs0())
                        xml.write(xmla_tools.generate_slicer_axis())

                    with xml.CellData:
                        xml.write(xmla_tools.generate_cell_data())

            return str(xml)


application = Application(
    [XmlaProviderService],
    'urn:schemas-microsoft-com:xml-analysis',
    in_protocol=XmlaSoap11(validator='soft'),
    out_protocol=XmlaSoap11(validator='soft'))

# validator='soft' or nothing, this is important because spyne doesn't support encodingStyle until now !!!!

wsgi_application = WsgiApplication(application)


def start_server(host='0.0.0.0', port=8000, write_on_file=False):
    """
    Start the xmla server.

    :param write_on_file:
     - False -> server logs will be displayed on console
     - True  -> server logs will be saved in file (~/olapy-data/logs)

    :return: server instance
    """

    imp.reload(sys)
    # reload(sys)  # Reload is a hack
    sys.setdefaultencoding('UTF8')

    from wsgiref.simple_server import make_server

    # log to the console
    # logging.basicConfig(level=logging.DEBUG")
    # log to the file
    # TODO FIX it with os
    if write_on_file:
        home_directory = expanduser("~")
        if not os.path.isdir(
                os.path.join(home_directory, 'olapy-data', 'logs')):
            os.makedirs(os.path.join(home_directory, 'olapy-data', 'logs'))
        logging.basicConfig(
            level=logging.DEBUG,
            filename=os.path.join(home_directory, 'olapy-data', 'logs',
                                  'xmla.log'))
    else:
        logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('spyne.protocol.xml').setLevel(logging.DEBUG)
    logging.info("listening to http://127.0.0.1:8000/xmla")
    logging.info("wsdl is at: http://localhost:8000/xmla?wsdl")
    server = make_server(host, port, wsgi_application)
    server.serve_forever()


if __name__ == '__main__':
    start_server(write_on_file=True)

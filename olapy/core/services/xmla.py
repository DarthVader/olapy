# -*- encoding: utf8 -*-
"""
The main Module to manage `XMLA <https://technet.microsoft.com/fr-fr/library/ms187178(v=sql.90).aspx>`_ \
requests and responses, and managing Spyne soap server.

"""
from __future__ import absolute_import, division, print_function

import imp
import logging
import os
import sys
from datetime import datetime
from os.path import expanduser
from wsgiref.simple_server import make_server

import click
import xmlwitch
from spyne import AnyXml, Application, Fault, ServiceBase, rpc
from spyne.const.http import HTTP_200
from spyne.error import InvalidCredentialsError
from spyne.protocol.soap import Soap11
from spyne.server.http import HttpTransportContext
from spyne.server.wsgi import WsgiApplication

from olapy.core.mdx.tools.config_file_parser import ConfigParser
from olapy.core.mdx.tools.olapy_config_file_parser import DbConfigParser
from sqlalchemy import create_engine

from olapy.core.mdx.executor.execute import MdxEngine
from ..services.models import DiscoverRequest, ExecuteRequest, Session
from .xmla_discover_tools import XmlaTools
from .xmla_execute_tools import XmlaExecuteTools
from .xmla_execute_xsds import execute_xsd


class XmlaSoap11(Soap11):
    """XHR does not work over https without this patch"""

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
    The main class to activate SOAP services between xmla clients and olapy.
    """

    # IMPORTANT : all XSD and SOAP responses are written manually (not generated by Spyne lib)
    # because Spyne doesn't support encodingStyle and other namespaces required by Excel,
    # check it <http://stackoverflow.com/questions/25046837/the-encodingstyle-attribute-is-not-allowed-in-spyne>
    #
    # NOTE : some variables and functions names shouldn't respect naming convention here
    # because we need to create the xmla response (generated by spyne) with the same variable names,
    # and then, xmla requests from excel can be reached
    # thus make life easier.

    @rpc(
        DiscoverRequest,
        _returns=AnyXml,
        _body_style="bare",
        _out_header=Session,
        _throws=InvalidCredentialsError,
    )
    def Discover(ctx, request):
        """The first principle function of xmla protocol.

        :param request: :class:`DiscoverRequest` object

        :return: XML Discover response as string

        """
        # ctx is the 'context' parameter used by Spyne
        # (which cause problems when we want to access xmla_provider instantiation variables)

        xmla_tools = ctx.app.config['xmla_tools']
        ctx.out_header = Session(SessionId=str(xmla_tools.session_id))
        config_parser = xmla_tools.executor.cube_config
        if config_parser and config_parser['xmla_authentication'] and ctx.transport.req_env['QUERY_STRING'] != 'admin':
            raise InvalidCredentialsError(
                fault_string='You do not have permission to access this resource',
                fault_object=None,
            )

        method_name = request.RequestType.lower() + '_response'
        method = getattr(xmla_tools, method_name)

        if request.RequestType == "DISCOVER_DATASOURCES":
            return method()
        return method(request)

    # Execute function must take 2 argument ( JUST 2 ! ) Command and Properties
    # we encapsulate them in ExecuteRequest object
    @rpc(
        ExecuteRequest,
        _returns=AnyXml,
        _body_style="bare",
        _out_header=Session,
    )
    def Execute(ctx, request):
        """The second principle function of xmla protocol.

        :param request: :class:`ExecuteRequest` object Execute.
        :return: XML Execute response as string
        """

        xmla_tools = ctx.app.config['xmla_tools']
        ctx.out_header = Session(SessionId=str(xmla_tools.session_id))
        mdx_query = request.Command.Statement.encode().decode('utf8')
        if mdx_query == '':
            # check if command contains a query
            xml = xmlwitch.Builder()
            with xml['return']:
                xml.root(xmlns="urn:schemas-microsoft-com:xml-analysis:empty")

            return str(xml)

        else:
            xmla_tools.change_catalogue(
                request.Properties.PropertyList.Catalog, )
            xml = xmlwitch.Builder()
            executor = xmla_tools.executor

            # Hierarchize
            if all(key in mdx_query for key in ['WITH MEMBER', 'strtomember', '[Measures].[XL_SD0]']):
                convert2formulas = True
            else:
                convert2formulas = False

            xmla_tools = XmlaExecuteTools(executor, mdx_query, convert2formulas)

            with xml['return']:
                with xml.root(
                    xmlns="urn:schemas-microsoft-com:xml-analysis:mddataset",
                    **{
                        'xmlns:xsd': 'http://www.w3.org/2001/XMLSchema',
                        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
                    }
                ):
                    xml.write(execute_xsd)
                    with xml.OlapInfo:
                        with xml.CubeInfo:
                            with xml.Cube:
                                xml.CubeName('Sales')
                                xml.LastDataUpdate(
                                    datetime.now().strftime(
                                        '%Y-%m-%dT%H:%M:%S', ),
                                    xmlns="http://schemas.microsoft.com/analysisservices/2003/engine",
                                )
                                xml.LastSchemaUpdate(
                                    datetime.now().strftime(
                                        '%Y-%m-%dT%H:%M:%S', ),
                                    xmlns="http://schemas.microsoft.com/analysisservices/2003/engine",
                                )
                        xml.write(xmla_tools.generate_cell_info())
                        with xml.AxesInfo:
                            xml.write(xmla_tools.generate_axes_info())
                            xml.write(xmla_tools.generate_axes_info_slicer())

                    with xml.Axes:
                        xml.write(xmla_tools.generate_xs0())
                        xml.write(xmla_tools.generate_slicer_axis())

                    with xml.CellData:
                        xml.write(xmla_tools.generate_cell_data())
            return str(xml)


home_directory = expanduser("~")
conf_file = os.path.join(home_directory, 'olapy-data', 'logs', 'xmla.log')


def get_mdx_engine(cube_config, sql_alchemy_uri, olapy_data,
                   source_type, direct_table_or_file, columns,
                   measures):
    print(direct_table_or_file)
    print(columns)
    print(measures)
    sqla_engine = None
    if sql_alchemy_uri:
        sqla_engine = create_engine(sql_alchemy_uri)
    executor = MdxEngine(olapy_data_location=olapy_data, source_type=source_type,
                         cube_config=cube_config, sqla_engine=sqla_engine)
    return executor


def get_spyne_app(xmla_tools):
    """

    :param xmla_tools: XmlaTools instance
    :return: spyne  Application
    """
    return Application(
        [XmlaProviderService],
        'urn:schemas-microsoft-com:xml-analysis',
        in_protocol=XmlaSoap11(validator='soft'),
        out_protocol=XmlaSoap11(validator='soft'),
        config={'xmla_tools': xmla_tools}
    )


def get_wsgi_application(mdx_engine):
    """

    :param mdx_engine: MdxEngine instance
    :return: Wsgi Application
    """
    xmla_tools = XmlaTools(mdx_engine)
    application = get_spyne_app(xmla_tools)

    # validator='soft' or nothing, this is important because spyne doesn't
    # support encodingStyle until now !!!!

    return WsgiApplication(application)


@click.command()
@click.option('--host', '-h', default='0.0.0.0', help='Host ip address.')
@click.option('--port', '-p', default=8000, help='Host port.')
@click.option('--write_on_file', '-wf', default=True,
              help='Write logs into a file or display them into the console. (True : on file)(False : on console)', )
@click.option('--log_file_path', '-lf', default=conf_file, help='Log file path. DEFAUL : ' + conf_file)
@click.option('--sql_alchemy_uri', '-sa', default=None, help="SQL Alchemy URI , **DON'T PUT THE DATABASE NAME** ")
@click.option('--olapy_data', '-od', default=None, help="Olapy Data folder location, Default : ~/olapy-data")
@click.option('--source_type', '-st', default='csv', help="Get cubes from where ( db | csv ), DEFAULT : csv")
@click.option('--db_config_file', '-dbc', default=os.path.join(home_directory, 'olapy-data', 'olapy-config.yml'),
              help="Database configuration file path, DEFAULT : " +
                   os.path.join(home_directory, 'olapy-data', 'olapy-config.yml'))
@click.option('--cube_config_file', '-cbf',
              default=os.path.join(home_directory, 'olapy-data', 'cubes', 'cubes-config.yml'),
              help="Cube config file path, DEFAULT : " +
                   os.path.join(home_directory, 'olapy-data', 'cubes', 'cubes-config.yml'))
@click.option('--direct_table_or_file', '-tf', default=None,
              help="File path or db table name if you want to construct cube from a single file (table)")
@click.option('--columns', '-c', default=None,
              help="To explicitly specify columns if (construct cube from a single file), columns order matters ")
@click.option('--measures', '-m', default=None,
              help="To explicitly specify measures if (construct cube from a single file)")
def runserver(host, port, write_on_file, log_file_path, sql_alchemy_uri, olapy_data, source_type, db_config_file,
              cube_config_file, direct_table_or_file, columns, measures):
    """
    Start the xmla server.
    """
    try:
        imp.reload(sys)
        # reload(sys)  # Reload is a hack
        sys.setdefaultencoding('UTF8')
    except Exception:
        pass

    cube_config = None
    if cube_config_file:
        cube_config_file_parser = ConfigParser()
        cube_config = cube_config_file_parser.get_cube_config(cube_config_file)

    sqla_uri = None
    if 'db' in source_type:
        if sql_alchemy_uri:
            # just uri, and inside XmlaTools we gonna to change uri if cube changes and the create_engine
            sqla_uri = sql_alchemy_uri
        else:
            # if uri not passed with params, look up in the olapy-data config file
            db_config = DbConfigParser()
            sqla_uri = db_config.get_db_credentials(db_config_file)

    mdx_engine = get_mdx_engine(cube_config=cube_config, sql_alchemy_uri=sqla_uri, olapy_data=olapy_data,
                                source_type=source_type, direct_table_or_file=direct_table_or_file, columns=columns,
                                measures=measures)

    wsgi_application = get_wsgi_application(mdx_engine)

    # log to the console
    # logging.basicConfig(level=logging.DEBUG")
    # log to the file
    if write_on_file:
        if not os.path.isdir(
            os.path.join(home_directory, 'olapy-data', 'logs'),
        ):
            os.makedirs(os.path.join(home_directory, 'olapy-data', 'logs'))
        logging.basicConfig(level=logging.DEBUG, filename=log_file_path)
    else:
        logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('spyne.protocol.xml').setLevel(logging.DEBUG)
    logging.info("listening to http://127.0.0.1:8000/xmla")
    logging.info("wsdl is at: http://localhost:8000/xmla?wsdl")
    server = make_server(host, port, wsgi_application)
    server.serve_forever()

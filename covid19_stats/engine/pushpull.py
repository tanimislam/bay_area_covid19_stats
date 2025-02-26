import os, sys, numpy, mutagen.mp4, datetime, getpass, time
import json, requests, mimetypes, logging, subprocess
from itertools import chain
from urllib.parse import urljoin
from fabric import Connection
from shutil import which

def _find_valid_png_files( init_png_files, prefixes = [ 'cds', 'cases', 'death' ] ):
    assert(all(map(os.path.isfile, init_png_files)))
    assert( len(list(map(os.path.basename, init_png_files))) ==
           len(set(map(os.path.basename, init_png_files))))
    def _is_valid_png_file( fname ):
        bname = os.path.basename( fname )
        if not bname.startswith( 'covid19_' ): return False
        if not bname.endswith( '_LATEST.png' ): return False
        if not any(map(lambda pfx: '_%s_' % pfx in bname, prefixes)): return False
        return True
    valid_png_files = set(filter(_is_valid_png_file, init_png_files))
    return valid_png_files

def _find_valid_mp4_files( init_mp4_files ):
    assert(all(map(os.path.isfile, init_mp4_files)))
    assert(len(list(map(os.path.basename, init_mp4_files))) ==
           len(set(map(os.path.basename, init_mp4_files))))
    def _is_valid_mp4_file( fname ):
        bname = os.path.basename( fname )
        if not bname.startswith( 'covid19_' ): return False
        if not bname.endswith( '_LATEST.mp4' ): return False
        return True
    valid_mp4_files = set(filter(_is_valid_mp4_file, init_mp4_files))
    return valid_mp4_files

def _get_status_mp4_file( mp4_file ):
    mp4tags = mutagen.mp4.MP4( mp4_file )
    region, display_type, date_s = map(lambda tok: tok.strip(), mp4tags[ '\xa9nam' ][ 0 ].split(','))
    date_p = datetime.datetime.strptime( date_s, '%d-%m-%Y' ).date( )
    region_type = mp4tags[ '\xa9alb' ][ 0 ]
    date_pub = datetime.datetime.strptime( mp4tags[ '\xa9day' ][ 0 ], '%d-%m-%Y' ).date( )
    assert( date_pub == date_p )
    return { 'region' : region, 'display type' : display_type, 'region type' : region_type,
            'date published' : date_p.strftime('%d %B %Y') }

def _get_status_png_file( png_file ):
    bname = os.path.basename( png_file )
    bname_strip = bname.replace('.png', '' ).strip( )
    region = '_'.join( bname_strip.split('_')[1:] )
    region = '_'.join( region.split('_')[:-2] )
    display_dict = { 'cases' : 'CASES', 'death' : 'DEATHS', 'cds' : 'ALL' }
    display_key = bname_strip.split('_')[-2].strip( ).lower( )
    assert( display_key in display_dict )
    return { 'region' : region, 'display type' : display_dict[ display_key ] }

def _get_dictionary_init( init_mp4_files ):
    valid_mp4_files = _find_valid_mp4_files( init_mp4_files )
    alldict_mp4_files = dict(map(lambda fname: ( fname, _get_status_mp4_file( fname ) ), valid_mp4_files ) )
    #
    ## now check that the fields do not clobber each other
    list_of_mp4_statuses = list(map(lambda entry: tuple(sorted(entry.items(), key = lambda tup: tup[0])), alldict_mp4_files.values()))
    set_of_mp4_statuses = set(map(lambda entry: tuple(sorted(entry.items(), key = lambda tup: tup[0])), alldict_mp4_files.values()))
    assert( len( list_of_mp4_statuses ) == len( set_of_mp4_statuses ) )
    #
    ## check that ALL dates are equal to each other
    alldates = set(map(lambda entry: entry[ 'date published' ], alldict_mp4_files.values()))
    assert( len(alldates ) == 1 )
    #
    ## now fill out the tree
    data_dict = { 'date published' : min(alldates), 'filemap' : { } }
    for fname in alldict_mp4_files:
        entry = alldict_mp4_files[ fname ]
        region_type = entry[ 'region type' ]
        if region_type not in data_dict:
            data_dict.setdefault( region_type, { } )
        region = entry[ 'region' ]
        if region not in data_dict[ region_type ]:
            data_dict[ region_type ].setdefault( region, { } )
        display_type = entry[ 'display type' ]
        if display_type not in data_dict[ region_type ][ region ]:
            assert( mimetypes.guess_type( fname )[ 0] is not None )
            data_dict[ region_type ][ region ][ display_type ] = { 'mp4' : os.path.basename( fname ) }
            data_dict[ 'filemap' ][ os.path.basename( fname ) ] = (
                fname, mimetypes.guess_type( fname )[ 0 ] )
    return data_dict

def _add_dictionary_pngs( data_dict, init_png_files ):
    valid_png_files = _find_valid_png_files( init_png_files )
    alldict_png_files = dict(map(lambda fname: ( fname, _get_status_png_file( fname ) ), valid_png_files ) )
    data_dict_new = data_dict.copy( )
    region_type_dict = dict(chain.from_iterable(map(lambda region_type:
                                                    map(lambda region: ( region, region_type ), data_dict[ region_type ] ),
                                                    set(data_dict) - set([ 'date published', 'filemap' ]) ) ) )
    #
    ## these PNG files regions MUST match those of the MP4 files
    assert(set( region_type_dict ) == set(map(lambda entry: entry[ 'region' ], alldict_png_files.values())))
    for fname in alldict_png_files:
        region = alldict_png_files[ fname ][ 'region' ]
        display_type = alldict_png_files[ fname ][ 'display type' ]
        region_type = region_type_dict[ region ]
        #
        ## PNG display type MUST be in original (MP4-populated) data_dict
        assert( display_type in data_dict_new[ region_type ][ region ] )
        assert( mimetypes.guess_type( fname )[ 0 ] is not None )
        data_dict_new[ region_type ][ region ][ display_type ][ 'png' ] = os.path.basename( fname )
        data_dict_new[ 'filemap' ][ os.path.basename( fname ) ] = (
            fname, mimetypes.guess_type( fname )[ 0 ] )
    return data_dict_new

def _add_dictionary_summary_json( data_dict, summary_json_file ):
    assert( os.path.basename( summary_json_file ) == 'covid19_topN_LATEST.json' )
    summary_json_data = json.load( open( summary_json_file, 'r' ) )
    topN = max(summary_json_data, key = lambda entry: entry['RANK'] )[ 'RANK' ]
    data_dict_new = data_dict.copy( )
    data_dict_new[ 'summary data' ] = summary_json_data
    data_dict_new[ 'topN' ] = topN
    return data_dict_new

def create_pushing_dictionary( init_mp4_files, init_png_files, summary_json_file ):
    data_dict = _get_dictionary_init( init_mp4_files )
    data_dict = _add_dictionary_pngs( data_dict, init_png_files )
    data_dict = _add_dictionary_summary_json( data_dict, summary_json_file )
    return data_dict

def verify_login_ssh( ssh_connection_info, remote_directory = None ):
    username = ssh_connection_info[ 'username' ]
    ssh_password = ssh_connection_info[ 'password' ]
    server = ssh_connection_info[ 'server' ]
    with Connection(
        server, user = username, connect_kwargs = { 'password' : ssh_password } ) as conn:
        if 'key_filename' in conn.connect_kwargs:
            conn.connect_kwargs.pop( 'key_filename' )
        _ = conn.run( 'echo', hide = True )
        if remote_directory is None: return conn.is_connected
        try:
            _ = conn.run( 'ls %s' % remote_directory, hide = True )
            return conn.is_connected
        except:
            return False

def _post_to_server_verify(
    covid19_verify_endpoint,
    data_dict,
    remote_directory,
    connection_info ):
    assert( connection_info[ 'type' ] in ( 'http', 'ssh' ) )
    
    def _verify_data_https( ):
        assert( connection_info[ 'type' ] == 'http' )
        verify = True
        if 'verify' in connection_info: verify = connection_info[ 'verify' ]
        covid19_server_prefix = connection_info[ 'server' ]
        user_email = connection_info[ 'user email' ]
        password = connection_info[ 'password' ]
        #
        ## now check with server, endpoint must consume POST request,
        final_verify_endpoint = urljoin( covid19_server_prefix, covid19_verify_endpoint )
        response = requests.post(
            final_verify_endpoint, auth = ( user_email, password ), verify = verify,
            json = data_dict, params = { 'directory' : remote_directory } )
        if response.status_code == 401:
            return { 'message' : "ERROR, passing useremail=%s, password=XXXXX to %s with VERIFY=%s DID NOT WORK." % (
                user_email, final_verify_endpoint, verify ) }
        if response.status_code != 200: # failure mode
            error_message = response.content
            return { 'message' : "ERROR, data_dict failed for this reason: %s." % ( error_message ),
                    'status code' : response.status_code }
        return { 'message' : 'SUCCESS' }

    def _verify_data_ssh_https( ):
        assert( connection_info[ 'type' ] == 'ssh' )
        username = connection_info[ 'username' ]
        ssh_password = connection_info[ 'password' ]
        server = connection_info[ 'server' ]
        with Connection( server, user = username, connect_kwargs = { 'password' : ssh_password } ) as conn:
            if 'key_filename' in conn.connect_kwargs:
                conn.connect_kwargs.pop( 'key_filename' )
            _ = conn.run( 'echo', hide = True )
            with conn.forward_local( local_port = 31999, remote_port = 443 ):
                final_verify_endpoint = urljoin( 'https://localhost:31999', covid19_verify_endpoint )
                response = requests.post(
                    final_verify_endpoint, auth = ( username, ssh_password ), verify = False,
                    json = data_dict )
                if response.status_code == 401:
                    return { 'message' : "ERROR, passing username=%s, password=XXXXX to %s DID NOT WORK." % (
                        username, final_verify_endpoint ) }
                if response.status_code != 200: # failure mode
                    error_message = response.content
                    return { 'message' : "ERROR, data_dict failed for this reason: %s." % ( error_message ),
                            'status code' : response.status_code }
                return { 'message' : 'SUCCESS' }
    #
    ## check our data has a good format, life is hard, because checking for SSH tunneling connectivity
    if connection_info[ 'type' ] == 'http': return _verify_data_https( )
    #    
    if not verify_login_ssh( connection_info, remote_directory = remote_directory ):
        return { 'message' : "ERROR, could not connect to SSH server=%s, username=%s." % (
            connection_info[ 'server' ], connection_info[ 'username' ] ) }
    return _verify_data_ssh_https( )

def _post_to_server_process(
    covid19_process_endpoint,
    data_dict,
    remote_directory,
    connection_info ):
    #
    ## check whether to do SSH
    if connection_info[ 'type' ] == 'ssh':
        assert( len(set(['server','username','password']) - set(connection_info)) == 0 )
        if not verify_login_ssh( connection_info, remote_directory = remote_directory ):
            return { 'message' : "ERROR, could not connect to SSH server=%s, username=%s." % (
                connection_info[ 'server' ], connection_info[ 'username' ] ) }
        
    #
    ## now do the processing
    def _get_post_file_entry( num, filename ):
        assert( filename in data_dict[ 'filemap' ] )
        file_type = 'video'
        if filename.endswith('png'):
            file_type = 'image'
        file_type = '%s_%02d' % ( file_type, num )
        fullpath, mimetype = data_dict[ 'filemap' ][ filename ]
        return ( file_type, ( filename, open( fullpath, 'rb' ).read( ), mimetype ) )
    file_list_processed = list(map(lambda tup: _get_post_file_entry( tup[0], tup[1] ),
                                   enumerate( sorted( data_dict[ 'filemap' ] ) ) ) )
    file_list_processed.append(
        ( 'json', ( 'data_dict.json', json.dumps( data_dict ), 'application/json' ) ) )
    #
    def _process_and_respond( final_process_endpoint, user, passwd, verif, remote ):
        big_ass_response = requests.post(
            final_process_endpoint, auth = ( user, passwd ), verify = verif,
            files = file_list_processed, params = { 'directory' : remote } )
        try: return big_ass_response.json( )
        except:
            return { 'message' : big_ass_response.content, 'status code' : big_ass_response.status_code }

    #
    ## if ssh
    if connection_info[ 'type' ] == 'ssh':
        username = connection_info[ 'username' ]
        ssh_password = connection_info[ 'password' ]
        server = connection_info[ 'server' ]
        with Connection( server, user = username, connect_kwargs = { 'password' : ssh_password } ) as conn:
            if 'key_filename' in conn.connect_kwargs:
                conn.connect_kwargs.pop( 'key_filename' )
            _ = conn.run( 'echo', hide = True )
            with conn.forward_local( local_port = 31999, remote_port = 443 ):
                final_process_endpoint = urljoin( 'https://localhost:31999', covid19_process_endpoint )
                return _process_and_respond( final_process_endpoint, username, ssh_password, False, remote_directory )

    #
    ## otherwise http/https
    user_email = connection_info[ 'user email' ]
    password = connection_info[ 'password' ]
    verify = True
    if 'verify' in connection_info: verify = connection_info[ 'verify' ]
    covid19_server_prefix = connection_info[ 'server' ]
    final_process_endpoint = urljoin( covid19_server_prefix, covid19_process_endpoint )
    return _process_and_respond( final_process_endpoint, user_email, password, verify, remote_directory )

def post_to_server(
    covid19_process_endpoint,
    covid19_verify_endpoint,
    data_dict,
    remote_directory,
    connection_info ):

    assert( connection_info[ 'type' ] in ( 'ssh', 'http' ) )
    
    if connection_info[ 'type' ] == 'ssh':
        try:
            status = verify_login_ssh( connection_info, remote_directory )
            if not status:
                return { 'messages' : 'ERROR: could not set up SSH connection to server=%s and user=%s.' % (
                    connection_info[ 'server' ], connection_info['username'] ) }
            logging.debug('WAS ABLE TO PROPERLY SET UP SSH CONNECTION: %s.' % connection_info )
        except Exception as e:
            return { 'messages' : 'ERROR: failed SSH connection with this exception: %s.' % str( e ) }

    def _get_message( pid ):
        try:
            lines = list(filter(lambda line: len(line.strip()) != 0 and line.strip().startswith('%d' % pid ),
                                subprocess.check_output([ 'ps', '%d' % pid ]).decode('utf8').split('\n')))
            if len( lines ) == 0: return ''
            return lines[-1].strip( )
        except Exception as e:
            logging.info( 'WAS UNABLE TO FIND THE SSH TUNNEL THAT ALLOWS US TO SEND THE DATA TO THE REST ENDPOINT. ERROR MESSAGE: %s' % (
                str( e ) ) )
            return ''
        
    def _setup_ssh_tunnel( ssh_connection_info ):
        assert( ssh_connection_info[ 'type' ] == 'ssh' )
        sshpass_exec = which( 'sshpass' )
        ssh_exec = which( 'ssh' )
        if not all(map(lambda exc: exc is not None, ( sshpass_exec, ssh_exec ) ) ):
            return 'Error, could not find sshpass AND ssh executables.', None
        username = ssh_connection_info[ 'username' ]
        password = ssh_connection_info[ 'password' ]
        server = ssh_connection_info[ 'server' ]
        #
        ## set up the tunnel
        proc = subprocess.Popen([ sshpass_exec, '-p', password, ssh_exec, '-fnN', '-L', '31999:localhost:443',
                                 '%s@%s' % ( username, server ) ], stdout = subprocess.PIPE, stderr = subprocess.STDOUT )
        #stdout_val, stderr_val = proc.communicate( )
        time.sleep( 0.5 ) # because maybe noncooperation?
        all_output_lines = list(filter(lambda line: len( line.strip( ) ) != 0,
                                       subprocess.check_output(['ps', '-fu', getpass.getuser( ) ] ).decode('utf8').split('\n') ) )
        valid_pids = sorted(map(lambda line: int(line.split()[1]),
                                filter(lambda line: '31999:localhost:443' in line and '%s@%s' % ( username, server ) in line,
                                    all_output_lines ) ) )
        logging.info('valid pids for candidate SSH connections: %s.' % valid_pids )
        if len( valid_pids ) == 0:
            return "ERROR: Tunneling SSH connection to %s with username=%s could not be established." % (
                server, username ), None
        return 'SUCCESS', min( valid_pids )

    act_connection_info = connection_info
    if connection_info['type'] == 'ssh':
        status, pid_ssh_tunnel = _setup_ssh_tunnel( connection_info )
        if status != 'SUCCESS':
            return { 'messages' : [ status ] }
        act_connection_info = {
            'type' : 'http',
            'server' : 'https://localhost:31999/',
            'verify' : False,
            'password' : connection_info[ 'password' ],
            'user email' : connection_info[ 'username' ] }
    #
    message_here = _post_to_server_verify(
        covid19_verify_endpoint, data_dict, remote_directory,
        connection_info = act_connection_info )
    if message_here[ 'message' ] != 'SUCCESS':
        return { 'messages' : [ message_here[ 'message' ] ] }

    message_here = _post_to_server_process(
        covid19_process_endpoint, data_dict, remote_directory,
        connection_info = act_connection_info )

    if connection_info[ 'type' ] == 'ssh':
        try:
            username = connection_info[ 'username' ]
            server = connection_info[ 'server' ]
            all_output_lines = list(filter(lambda line: len( line.strip( ) ) != 0,
                                        subprocess.check_output(['ps', '-fu', getpass.getuser( ) ] ).decode('utf8').split('\n') ) )
            valid_pids = sorted(map(lambda line: int(line.split()[1]),
                                    filter(lambda line: '31999:localhost:443' in line and '%s@%s' % ( username, server ) in line,
                                        all_output_lines ) ) )
            for pid_ssh in valid_pids:
                final_statement = subprocess.check_output([ 'kill', '-9', '%d' % pid_ssh ])
        except Exception as e:
            failing_message = "ERROR, could not kill SSH connection located in processes. Exception: %s." % (
                str( e ) )
            logging.error( failing_message )
            message_here['messages'].append( failing_message )

    return message_here

def verify_summary_data( summary_data ):
    assert( isinstance( summary_data, list ) )
    keys_must_be_here = {'FIRST INC.',
                         'MAX CASE COUNTY',
                         'MAX CASE COUNTY NAME',
                         'NAME',
                         'NUM CASES',
                         'NUM DAYS',
                         'NUM DEATHS',
                         'POPULATION',
                         'PREFIX',
                         'RANK'}
    assert(all(map(lambda entry: len( keys_must_be_here - set(entry) ) == 0, summary_data ) ) )
    #
    ## now get ranks
    ranks = list(map(lambda entry: entry['RANK'], summary_data ) )
    assert(all(map(lambda rank: isinstance(rank, int), ranks)))
    assert( len( ranks ) == len( set( ranks ) ) )
    assert( set( ranks ) == set(range(1, len(ranks)+1)))
    #
    ## dont do further checks here
    return True

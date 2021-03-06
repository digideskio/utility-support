#!/usr/bin/python
"""
resolve-by-mlabns is a pipe-backend for pdns.  On startup, pdns spawns backend
processes that read queries from stdin, and write answers to stdout. pdns does
all the remaining heavy lifting of the DNS protocol.

resolve-by-mlabns recognizes two types of query: A and SOA.

'A' record replies are generated by passing the remote IP in the query directly
to mlab-ns.  The A records have a short TTL, so clients receive up-to-date
server status information from mlab-ns.

'SOA' record replies are generated from a static template, like DONAR.
"""

import json
import socket
import sys
from sys import stdin, stdout, exit
import time
import urllib2

RECORD_TTL=300
LOCAL_HOSTNAME=socket.gethostname()

# NOTE: could add metro
DOMAIN = "donar.measurement-lab.org"
NDT_HOSTLIST = [ "ndt.iupui."+DOMAIN, DOMAIN ]

def log_msg(msg):
    if msg is None: return
    stdout.write("LOG\t"+msg.replace('\t', ' '))

def data_msg(msg):
    if msg is None: return
    log_msg("REPLY: "+msg)
    stdout.write("DATA\t"+msg)

def query_to_dict(query_str):
    fields = query_str.split("\t")
    ret = {}
    if len(fields) == 6:
        (kind, qname, qclass, qtype, id, ip) = fields
    elif len(fields) == 2:
        (qname, qclass, qtype, ip) = (None, None, None, None)
        (kind, id) = fields
    else:
        msg = "FAILED to parse query: %s\n" % query_str
        log_msg(msg)
        return None
    ret['kind'] = kind
    ret['name'] = qname
    ret['class'] = qclass
    ret['type'] = qtype
    ret['id'] = id
    ret['remote_ip'] = ip
    ret['ttl'] = RECORD_TTL
    return ret

def soa_record(query):
    """ Formats an SOA record using fields in 'query' and global values.
    Return string is suitable for printing in a 'DATA' reply to pdns.

    Example (split across two lines for clarity):
        ndt.iupui.donar.measurement-lab.org IN SOA 60 -1 localhost. 
            support.measurementlab.net. 2013092700 1800 3600 604800 3600\\n

    TODO: these values are like DONAR, but confirm that the fields make sense.
    """
    reply  = DOMAIN+".\t"
    reply += "%(class)s\t"
    reply += "SOA\t"
    reply += "%(ttl)s\t"
    reply += "-1\t"
    reply += "localhost. "
    reply += "support.measurementlab.net. "
    reply += "2013092700 1800 3600 604800 3600\n"
    return reply % query

def a_record(query, ipaddr):
    """ Formats an A record using fields in 'query' and ipaddr, suitable for
    printing in a 'DATA' reply to pdns.

    Example:
        ndt.iupui.donar.measurement-lab.org IN A 60 -1 192.168.1.2\\n
    """
    reply  = "%(name)s\t"
    reply += "%(class)s\t"
    reply += "A\t"
    reply += "%(ttl)s\t"
    reply += "%(id)s\t"
    reply += ipaddr+"\n"
    return reply % query

def mlabns_a_record(query):
    """ issue lookup to mlab-ns with given 'remote_ip' in 'query' """
    try:
        url_fmt = 'http://ns.measurementlab.net/ndt?ip=%s&format=json' 
        url = url_fmt % query['remote_ip']
        request = urllib2.Request(url)
        request.add_header('User-Agent','nodar/1.0 from '+LOCAL_HOSTNAME)
        resp = urllib2.build_opener().open(request)
    except:
        msg_fmt = "Exception during query for /ndt?ip=%s&format=json : %s\n"
        msg = msg_fmt % (ip, str(e))
        log_msg(msg)
        return None
 
    ns_resp = json.load(resp)
    if 'ip' not in ns_resp or len(ns_resp['ip']) == 0:
        msg = "mlab-ns response missing 'ip' field: %s\n" % ns_resp
        log_msg(msg)
        return None
        
    # TODO: if len > 1, return all. Are multiple IPs supported by mlab-ns?
    ndt_ip = ns_resp['ip'][0]
    return a_record(query, ndt_ip)

def ns_record(query, host):
    """ Formats an NS record using fields in 'query' and global values.
    Return string is suitable for printing in a 'DATA' reply to pdns.

    Example:
        donar.measurement-lab.org IN NS 300 -1 <host>\\n
    """
    reply  = DOMAIN+"\t"
    reply += "%(class)s\t"
    reply += "NS\t"
    reply += "%(ttl)s\t"
    reply += "%(id)s\t"
    reply += host+"\n"
    return reply % query

def handle_ns_records(query):
    """ Hints for sub-zone NS records are provided on the parent zone.  In our
    case, measurement-lab.org includes NS records for donar.measurement-lab.org
    that point to nodar servers running in slices.

    However, the authoritative NS records should be served by the sub-zone.
    This function handles parsing the config file in /etc/donar.txt and
    generating NS replies for donar.measurement-lab.org
    """
    # TODO: need better way to keep ns records consistent across zone-cuts
    # TODO: from measurement-lab.org zone to each nodar server.
    try:
        DONAR_HOSTS="/etc/donar.txt"
        host_list = open(DONAR_HOSTS, 'r').readlines()
    except:
        log_msg("Failed to open: %s\n" % DONAR_HOSTS)
        return

    try:
        # NOTE: use up to six hosts (shorter is ok)
        for host in host_list[:6]:
            data_msg(ns_record(query, "utility.mlab."+host.strip()))
    except:
        log_msg("Failed on: %s\n" % query)

def main():
    # HANDSHAKE with pdns
    while True:
        helo = stdin.readline()
        if "HELO" in helo:
            stdout.write("OK\tM-Lab Backend\n")
            stdout.flush()
            break
        # NOTE: recommended behavior is to not exit, try again, and wait to be 
        # terminated. http://doc.powerdns.com/html/backends-detail.html
        print "FAIL"

    # PROCESS QUERIES from pdns
    while True:
        query_str = stdin.readline()
        if query_str == "": break # EOF

        query_str = query_str.strip()
        query = query_to_dict(query_str)
        log_msg("INPUT: %s\n" % query_str)

        # NOTE: if this is a valid query, for a name we support.
        if (query is not None and query['kind'] == "Q" and
            query['name'] in NDT_HOSTLIST):

            if query['type']=="SOA":
                data_msg(soa_record(query))
            if query['type'] in [ "ANY", "A" ]:
                data_msg(mlabns_a_record(query))
                data_msg(mlabns_a_record(query))
            if query['type'] in [ "ANY", "NS" ]:
                handle_ns_records(query)

        stdout.write("END\n")
        stdout.flush()

if __name__ == "__main__":
    main()

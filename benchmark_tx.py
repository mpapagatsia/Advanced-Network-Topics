#!/usr/bin/env python
#
# Copyright 2010,2011,2013 Free Software Foundation, Inc.
# 
# This file is part of GNU Radio
# 
# GNU Radio is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# 
# GNU Radio is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with GNU Radio; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
# 
from oml4py import OMLBase

import sys, os

path = os.path.dirname(sys.argv[0]).split("share")[0] + "lib/python2.7/dist-packages"
sys.path.append(path)

os.environ['SHELL'] = "/bin/bash"
os.environ['LC_ALL'] = 'C'
os.environ['LANG'] = 'C'
os.environ['PYTHONPATH'] = os.path.dirname(sys.argv[0]).split("share")[0] +'lib/python2.7/dist-packages'
os.environ['PKG_CONFIG_PATH'] = os.path.dirname(sys.argv[0]).split("share")[0] +'lib/pkgconfig'

from gnuradio import gr
from gnuradio import blocks
from gnuradio import eng_notation
from gnuradio.eng_option import eng_option
from optparse import OptionParser

# From gr-digital
from gnuradio import digital

# from current dir
from transmit_path import transmit_path
from uhd_interface import uhd_transmitter, uhd_receiver
from receive_path import receive_path

import benchmark_rx
import time, struct
import socket

import bz2

#import os 
#print os.getpid()
#raw_input('Attach and press enter')

class my_top_block(gr.top_block):
    def __init__(self, modulator, options):
        gr.top_block.__init__(self)

        if(options.tx_freq is not None):
            # Work-around to get the modulation's bits_per_symbol
            args = modulator.extract_kwargs_from_options(options)
            symbol_rate = options.bitrate / modulator(**args).bits_per_symbol()
	    
            self.sink = uhd_transmitter(options.args, symbol_rate,
                                        options.samples_per_symbol, options.tx_freq,
                                        options.lo_offset, options.tx_gain,
                                        options.spec, options.antenna,
                                        options.clock_source, options.verbose)
            options.samples_per_symbol = self.sink._sps
            
        elif(options.to_file is not None):
            sys.stderr.write(("Saving samples to '%s'.\n\n" % (options.to_file)))
            self.sink = blocks.file_sink(gr.sizeof_gr_complex, options.to_file)
        else:
            sys.stderr.write("No sink defined, dumping samples to null sink.\n\n")
            self.sink = blocks.null_sink(gr.sizeof_gr_complex)

        # do this after for any adjustments to the options that may
        # occur in the sinks (specifically the UHD sink)
        self.txpath = transmit_path(modulator, options)

        self.connect(self.txpath, self.sink)
        print >> sys.stderr, options

global storage 
global data_list

global feedback_tb, tb
global n_rcvd, n_correct
global change_freq

# /////////////////////////////////////////////////////////////////////////////
#                                   main
# /////////////////////////////////////////////////////////////////////////////

def main():
    global storage
    global data_list
    global feedback_tb, tb
    global n_rcvd, n_correct
    global change_freq

    change_freq = 0    
    n_rcvd = 0
    n_correct = 0

    data_list = []
    
    def send_pkt(payload='', eof=False):
        return tb.txpath.send_pkt(payload, eof)
    
    def rx_callback(ok, payload):
        global data_list
        global n_rcvd, n_correct
        global change_freq

        (pktno,) = struct.unpack('!H', payload[0:2])
        data = payload[2:]

        n_rcvd += 1

        if ok:
            data_list = []
            
            # if a packet with packet number 1 has arrived, frequency has to change
            if pktno == 1:
                change_freq = 1
            # else save list with lost packets
            if pktno > 2:
                n_correct += 1
                #remove "-" delimiter
                temp_list = data.split("-")
                
                for i in range(len(temp_list)):
                    if temp_list[i] != "":
                       data_list.append(int(temp_list[i]))
#        print "pktno = %d, n_rcvd = %d, n_correct = %d, change freq = %d" % (pktno, n_rcvd, n_correct, change_freq) 

    def feedback_rx():

        demods = digital.modulation_utils.type_1_demods()

        # Create Options Parser:
        parser = OptionParser (option_class=eng_option, conflict_handler="resolve")
        expert_grp = parser.add_option_group("Expert")

        # Change modulation from to gmsk
        parser.add_option("-m", "--modulation", type="choice", choices=demods.keys(),
                          default='gmsk',
                          help="Select modulation from: %s [default=%%default]"
                                % (', '.join(demods.keys()),))
        parser.add_option("","--from-file", default=None,
                          help="input file of samples to demod")
        parser.add_option("-E", "--exp-id", type="string", default="test",
                          help="specify the experiment ID")
        parser.add_option("-N", "--node-id", type="string", default="rx",
                          help="specify the experiment ID")
        parser.add_option("","--server", action="store_true", default=False,
                          help="To take data from the server")

        receive_path.add_options(parser, expert_grp)
        uhd_receiver.add_options(parser)
        for mod in demods.values():
            mod.add_options(expert_grp)

        (options, args) = parser.parse_args ()
        options.antenna = "RX2"
        options.rx_freq = options.rx_freq - 1e6
        options.tx_freq = options.tx_freq - 1e6
        options.bitrate = 300000
        
        rtb = benchmark_rx.my_top_block(demods[options.modulation], rx_callback, options)

        return rtb

    mods = digital.modulation_utils.type_1_mods()
   
    #build the feedback receiver
    feedback_tb = feedback_rx()

    parser = OptionParser(option_class=eng_option, conflict_handler="resolve")
    expert_grp = parser.add_option_group("Expert")
    
    #Change modulation to gmsk
    parser.add_option("-m", "--modulation", type="choice", choices=mods.keys(),
                      default='gmsk',
                      help="Select modulation from: %s [default=%%default]"
                            % (', '.join(mods.keys()),))

    parser.add_option("-s", "--size", type="eng_float", default=1500,
                      help="set packet size [default=%default]")
    parser.add_option("-M", "--megabytes", type="eng_float", default=1000.0,
                      help="set megabytes to transmit [default=%default]")
    parser.add_option("","--discontinuous", action="store_true", default=False,
                      help="enable discontinous transmission (bursts of 5 packets)")
    parser.add_option("","--from-file", default=None,
                      help="use intput file for packet contents")
    parser.add_option("","--to-file", default=None,
                      help="Output file for modulated samples")
    parser.add_option("-E", "--exp-id", type="string", default="test",
                          help="specify the experiment ID")
    parser.add_option("-N", "--node-id", type="string", default="tx",
                          help="specify the experiment ID")
    parser.add_option("","--server", action="store_true", default=False,
                      help="To take data from the server")

    transmit_path.add_options(parser, expert_grp)
    uhd_transmitter.add_options(parser)

    for mod in mods.values():
        mod.add_options(expert_grp)

    (options, args) = parser.parse_args ()

    center_freq = options.tx_freq

    options.rx_freq = options.rx_freq + 1e6
    options.tx_freq = options.tx_freq + 1e6

    omlDb = OMLBase("gnuradiorx",options.exp_id,options.node_id,"tcp:nitlab3.inf.uth.gr:3003")
    omlDb.addmp("packets", "type:string value:long")

    omlDb.start()


    if len(args) != 0:
        parser.print_help()
        sys.exit(1)
           
    if options.from_file is not None:
        source_file = open(options.from_file, 'r')

    # build the graph
    tb = my_top_block(mods[options.modulation], options)
		
    r = gr.enable_realtime_scheduling()
    if r != gr.RT_OK:
        print "Warning: failed to enable realtime scheduling"

    tb.start()                       # start flow graph

    feedback_tb.start()
    
    start = time.time()
    
    # generate and send packets
    nbytes = int(1e6 * options.megabytes)
    n = 0
    pktno = 0
    pkt_size = int(options.size)

    storage = []
    hop = 0

    # connect to server
    if options.server:
    	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	server_address = ('10.0.1.200', 51000)
    	print >>sys.stderr, 'connecting to %s port %s' % server_address
    	sock.connect(server_address)

    while n < nbytes or options.server:
	if options.server:
            data = "";
            while len(data) < pkt_size:
                data += sock.recv(pkt_size - len(data))
                if data == '':
                    # No more data received from server
                    sock.close()
                    break;
        elif options.from_file is None:
            data = (pkt_size - 2) * chr(pktno & 0xff)
        else:
            data = source_file.read(pkt_size - 2)
            if data == '':
                break;
        # compress data to be sent
        compressed_data = bz2.compress(data)
        storage.append(compressed_data)

        # frequency hopping if needed
        if change_freq == 1 and hop == 0:
            feedback_tb.source.set_freq(center_freq + 1e6, 0)
            tb.sink.set_freq(center_freq - 1e6, 0)
            hop = 1

        payload = struct.pack('!H', pktno & 0xffff) + compressed_data
        send_pkt(payload)
        n += len(payload)
        sys.stderr.write('.')
        omlDb.inject("packets", ("sent", pktno))
        if options.discontinuous and pktno % 5 == 4:
            time.sleep(1)

        pktno += 1
        if pktno == 1000:
            sock.close()
            break     
        
    back_flag = 0    
#    send_pkt(eof=True)

    while True:
        temp = data_list

        if change_freq == 1 and hop == 0:
            feedback_tb.source.set_freq(center_freq + 1e6, 0)
            tb.sink.set_freq(center_freq - 1e6, 0)
            hop = 1

        if len(temp) != 0:
            for i in range(len(temp)):
                # if back_flag = 0, send packets from the beginning of the list
                if back_flag == 0:
                    payload_rt = struct.pack('!H', temp[i] & 0xffff) + storage[temp[i]]
                #else if back_flag = 1, send packets from the end of the list
                else:
                    pos = len(temp) - i - 1
                    payload_rt = struct.pack('!H', temp[pos] & 0xffff) + storage[temp[pos]]
                send_pkt(payload_rt)
            
            if back_flag == 0:
                back_flag = 1
            else:
                back_flag = 0
        else:
            for i in range(len(storage)):
                payload_rt = struct.pack('!H', i & 0xffff) + storage[i]
                send_pkt(payload_rt)


    tb.wait()                       # wait for it to finish

    feedback_tb.wait()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
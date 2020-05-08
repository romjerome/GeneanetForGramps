#!/usr/bin/python3
#
# GeneanetForGramps
#
# Copyright (C) 2020  Bruno Cornec
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the Affero GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#

# $Id: $

"""
Geneanet Gramplet
Import into Gramps persons from Geneanet
"""
#-------------------------------------------------------------------------
#
# Standard Python Modules
#
#-------------------------------------------------------------------------
import os
import time
import io
import sys

#------------------------------------------------------------------------
#
# GTK modules
#
#------------------------------------------------------------------------
from gi.repository import Gtk

from gramps.gen.plug import Gramplet
from gramps.gui.editors import EditPerson
from gramps.gen.errors import WindowActiveError, DatabaseError
from gramps.gen.display.name import displayer as name_displayer
from gramps.gen.datehandler import get_date
from gramps.gen.utils.db import get_birth_or_fallback, get_death_or_fallback

from gramps.gen.lib import (EventRoleType, FamilyRelType, Citation, EventType,\
 PlaceType,Person, AttributeType, NameType, NoteType)
from gramps.gen.utils.file import media_path_full, media_path, relative_path
from gramps.gen.const import GRAMPS_LOCALE as glocale
try:
    _trans = glocale.get_addon_translator(__file__)
except ValueError:
    _trans = glocale.translation
_ = _trans.gettext

import zipfile
import logging
from gramps.version import VERSION
from gramps.gen.config import config
from gramps.gen.display.place import displayer as _pd
from gramps.gen.utils.location import get_main_location
from gramps.gen.utils.place import conv_lat_lon

LOG = logging.getLogger("geneanetforgedcom")

# From gramps/plugins/importer/importgedcom.py
# The following code is necessary to ensure that when Help->Plugin
# Manager->Reload is executed, not only is the top-level exportgedcom file
# reloaded, but also the dependent libgedcom. This ensures that testing can have
# a quick turnround, without having to restart Gramps
module = __import__("gramps.plugins.lib.libgedcom",
                    fromlist=["gramps.plugins.lib"])   # why o why ?? as above!
import imp
imp.reload(module)

MIME2GED = {
    "image/bmp"   : "bmp",
    "image/gif"   : "gif",
    "image/jpeg"  : "jpeg",
    "image/x-pcx" : "pcx",
    "image/tiff"  : "tiff",
    "audio/x-wav" : "wav"
    }

LANGUAGES = {
    'cs' : 'Czech', 'da' : 'Danish','nl' : 'Dutch',
    'en' : 'English','eo' : 'Esperanto', 'fi' : 'Finnish',
    'fr' : 'French', 'de' : 'German', 'hu' : 'Hungarian',
    'it' : 'Italian', 'lt' : 'Latvian', 'lv' : 'Lithuanian',
    'no' : 'Norwegian', 'po' : 'Polish', 'pt' : 'Portuguese',
    'ro' : 'Romanian', 'sk' : 'Slovak', 'es' : 'Spanish',
    'sv' : 'Swedish', 'ru' : 'Russian',
    }

QUALITY_MAP = {
    Citation.CONF_VERY_HIGH : "3",
    Citation.CONF_HIGH      : "2",
    Citation.CONF_NORMAL    : "1",
    Citation.CONF_LOW       : "0",
    Citation.CONF_VERY_LOW  : "0",
}

GRAMPLET_CONFIG_NAME = "geneanetforgramps"
CONFIG = config.register_manager("geneanetforgramps")

CONFIG.register("preferences.include_ascendants", True)
CONFIG.register("preferences.include_descendants", True)
CONFIG.register("preferences.include_spouse", True)
CONFIG.load()

from lxml import html
import requests
import argparse
from datetime import datetime

ROOTURL = 'https://gw.geneanet.org/'
LEVELA = 0
LEVELC = 0
headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}

parser = argparse.ArgumentParser(description="Import Geneanet subtrees into Gramps")
parser.add_argument("-v", "--verbosity", action="count", default=0, help="Increase verbosity")
parser.add_argument("-a", "--ascendants", default=False, action='store_true', help="Includes ascendants (off by default)")
parser.add_argument("-d", "--descendants", default=False, action='store_true', help="Includes descendants (off by default)")
parser.add_argument("-s", "--spouse", default=False, action='store_true', help="Includes spouse (off by default)")
parser.add_argument("-l", "--level", default=1, type=int, help="Number of level to explore (1 by default)")
parser.add_argument("person", type=str, nargs='?', help="Url of the person to search in Geneanet")
args = parser.parse_args()

if args.verbosity >= 1:
    print("LEVEL: ",LEVELA)
#person = 'agnesy?lang=fr&n=queffelec&oc=17&p=marie+anne'
if args.person == None:
    person = 'agnesy?lang=fr&pz=hugo+mathis&nz=renard&p=marie+sebastienne&n=helgouach'
else:
    person = args.person

def convert_date(datetab):
    ''' Convert the Geneanet date format for birth/death/married lines
    into an ISO date format
    '''

    if args.verbosity >= 1:
        print("datetab received: ",datetab)

    if len(datetab) == 0:
        return("")
    idx = 0
    if datetab[0] == 'le':
        idx = 1
    if datetab[idx] == "1er":
        datetab[idx] = "1"
    bd1 = " "
    bd1 = bd1.join(datetab[idx:])
    bd2 = datetime.strptime(bd1, "%d %B %Y")
    return(bd2.strftime("%Y-%m-%d"))

def find_geneanet_person(person):
    ''' Use XPath to retrieve the details of a person
    Used example from https://gist.github.com/IanHopkinson/ad45831a2fb73f537a79
    and doc from https://www.w3schools.com/xml/xpath_axes.asp
    and https://docs.python-guide.org/scenarios/scrape/
    '''
    
    global LEVELA
    global LEVELC

    try:
        page = requests.get(ROOTURL+person)
        if args.verbosity >= 1:
            print("Return code: ",page.status_code)
    except:
        print("We failed to reach the server")
    else:
        if page.ok:
            try:
                tree = html.fromstring(page.content)
            except:
                print("Unable to perform HTM analysis")
    
            try:
                # Should return F or M
                sex = tree.xpath('//div[@id="person-title"]//img/attribute::alt')
            except:
                sex = 'I'
            try:
                name = tree.xpath('//div[@id="person-title"]//a/text()')
            except:
                name = ["", ""]
            try:
                birth = tree.xpath('//li[contains(., "Né")]/text()')
            except:
                birth = [""]
            try:
                death = tree.xpath('//li[contains(., "Décédé")]/text()')
            except:
                death = [""]
            try:
                parents = tree.xpath('//li[@style="vertical-align:middle;list-style-type:disc"]')
            except:
                parents = []
            try:
                spouse = tree.xpath('//ul[@class="fiche_union"]//li[@style="vertical-align:middle;list-style-type:disc"]')
            except:
                spouse = []
            print('-----------------------------------------------------------')
            print('Name (L%d): %s %s'%(LEVELA,name[0],name[1]))
            ld = convert_date(birth[0].split('-')[0].split()[1:])
            print('Birth: ', ld)
            print('Birth place: ', birth[0].split('-')[1].split(',')[0])
            print('Birth place code: ', birth[0].split('-')[1].split(',')[1])
            ld = convert_date(death[0].split('-')[0].split()[1:])
            print('Death: ', ld)
            print('Death place: ', death[0].split('-')[1].split(',')[0])
            print('Death place code: ', death[0].split('-')[1].split(',')[1])
            print('Sex: ', sex[0])
            for s in spouse:
                try:
                    sname = s.xpath('a/text()')[0]
                except:
                    sname = ""
                try:
                    married = s.xpath('em/text()')[0]
                except: 
                    married = ""
                try:
                    sref = s.xpath('a/attribute::href')[0]
                except:
                    sref = ""
                print('Spouse name: ', sname)
                print('Spouse ref: ', ROOTURL+sref)
                ld = convert_date(married.split(',')[0].split()[1:])
                print('Married: ', ld)
                print('Married place: ', married.split(',')[1])
                print('Married place code: ', married.split(',')[2])
                print('-----------------------------------------------------------')

                if args.spouse:
                    time.sleep(5)
                    find_geneanet_person(sref)

                children = s.xpath('ul/li[@style="vertical-align:middle;list-style-type:square;"]')
                cnum = 1
                for c in children:
                    try:
                        cname = c.xpath('a/text()')[0]
                    except:
                        cname = ""
                    try:
                        cref = c.xpath('a/attribute::href')[0]
                    except:
                        cref = ""
                    print('Child %d name (L%d): %s'%(cnum,LEVELA,cname))
                    print('Child %d ref: %s'%(cnum,ROOTURL+cref))
                    if args.descendants and LEVELC < args.level:
                        LEVELC = LEVELC + 1
                        time.sleep(5)
                        find_geneanet_person(cref)

                    cnum = cnum + 1

            for p in parents:
                if args.verbosity >= 1:
                    print(p.xpath('text()'))
                if p.xpath('text()')[0] == '\n':
                    try:
                        pname = p.xpath('a/text()')[0]
                    except:
                        pname = ""
                    try:
                        pref = p.xpath('a/attribute::href')[0]
                    except:
                        pref = ""
                    print('Parent name (L%d): %s'%(LEVELA,pname))
                    print('Parent ref: ', ROOTURL+pref)
                    print('-----------------------------------------------------------')

                    if args.ascendants and LEVELA < args.level:
                        LEVELA = LEVELA + 1
                        time.sleep(5)
                        find_geneanet_person(pref)

            print('-----------------------------------------------------------')
        else:
            print("We failed to be ok with the server")

def import_data(database, filename, user):

    global callback

    try:
        g = GeneanetParser(database)
    except IOError as msg:
        user.notify_error(_("%s could not be opened\n") % filename,str(msg))
        return

    try:
        status = g.find_geneanet_person(person)
    except IOError as msg:
        errmsg = _("%s could not be opened\n") % filename
        user.notify_error(errmsg,str(msg))
        return
    return ImportInfo({_("Results"): _("done")})

find_geneanet_person(person)
if args.verbosity >= 1:
    sys.exit("End for now")
sys.exit()

class GeneWebParser:
    def __init__(self, dbase, file):
        self.db = dbase

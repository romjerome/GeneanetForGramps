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

from gramps.gen.const import GRAMPS_LOCALE as glocale
try:
    _trans = glocale.get_addon_translator(__file__)
except ValueError:
    _trans = glocale.translation
_ = _trans.gettext

import logging
from gramps.gen.config import config
from gramps.gen.display.place import displayer as _pd
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import open_database
from gramps.gen.dbstate import DbState
from gramps.cli.grampscli import CLIManager
from gramps.gen.lib import Person, Name, Surname, NameType, Event, EventType, Date, Place, EventRoleType, EventRef, PlaceName, Family, ChildRef, FamilyRelType, Url, UrlType
#from gramps.gen.utils.location import get_main_location
#from gramps.version import VERSION

LOG = logging.getLogger("geneanetforgedcom")

TIMEOUT = 5

LANGUAGES = {
    'cs' : 'Czech', 'da' : 'Danish','nl' : 'Dutch',
    'en' : 'English','eo' : 'Esperanto', 'fi' : 'Finnish',
    'fr' : 'French', 'de' : 'German', 'hu' : 'Hungarian',
    'it' : 'Italian', 'lt' : 'Latvian', 'lv' : 'Lithuanian',
    'no' : 'Norwegian', 'po' : 'Polish', 'pt' : 'Portuguese',
    'ro' : 'Romanian', 'sk' : 'Slovak', 'es' : 'Spanish',
    'sv' : 'Swedish', 'ru' : 'Russian',
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

# Generic functions

def format_iso(date_tuple):
    """
    Format an iso date.
    """
    year, month, day = date_tuple
    # Format with a leading 0 if needed
    month = str(month).zfill(2)
    day = str(day).zfill(2)
    if year == None or year == 0:
       iso_date = ''
    elif month == None or month == 0:
       iso_date = str(year)
    elif day == None or day == 0:
        iso_date = '%s-%s' % (year, month)
    else:
        iso_date = '%s-%s-%s' % (year, month, day)
    return iso_date

def format_noniso(date_tuple):
    """
    Format an non-iso tuple into an iso date
    """
    day, month, year = date_tuple
    return(format_iso(year, month, day))

def get_gramps_date(person,evttype,db):
    '''
    Give back the date of the event related to the person
    '''

    if args.verbosity >= 3:
        print("EventType: %d"%(evttype))

    if evttype == EventType.BIRTH:
        ref = person.get_birth_ref()
    elif evttype == EventType.DEATH:
        ref = person.get_death_ref()
    elif evttype == EventType.MARRIAGE:
        ref = get_marriage_date(db,person)
    else:
        print("Didn't find a known EventType: ",evttype)
        return(None)

    if ref:
        if args.verbosity >= 3:
            print("Ref:",ref)
        try:
            event = db.get_event_from_handle(ref.ref)
        except:
            print("Didn't find a known ref for this ref date: ",ref)
            return(None)
        if event:
            if args.verbosity >= 3:
                print("Event:",event)
            date = event.get_date_object()
            tab = date.get_dmy()
            if args.verbosity >= 3:
                print("Found date:",tab)
            if len(tab) == 3:
                tab = date.get_ymd()
                if args.verbosity >= 3:
                    print("Found date2:",tab)
                ret = format_iso(tab)
            else:
                ret = format_noniso(tab)
            if args.verbosity >= 3:
                print("Returned date:",ret)
            return(ret)
        else:
            return(None)
    else:
        return(None)

def convert_date(datetab):
    ''' Convert the Geneanet date format for birth/death/married lines
    into an ISO date format
    '''

    if args.verbosity >= 3:
        print("datetab received:",datetab)

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
        
def get_child_list(db, person, spouse):
    "return list of children for given person or None"
    children = []
    cret = []
    for fam_handle in person.get_family_handle_list():
        fam = db.get_family_from_handle(fam_handle)
        for child_ref in fam.get_child_ref_list():
            # Adds only if this is the correct spouse
            children.append(child_ref.ref)
    if children:
        for c in children:
            c1 = db.get_person_from_handle(c)
            cret.append(c1)
        return (cret)
    return None

def get_marriage_list(db, person):
    "return list of marriages for given person or None"
    marriages = []
    for family_handle in person.get_family_handle_list():
        family = db.get_family_from_handle(family_handle)
        if int(family.get_relationship()) == FamilyRelType.MARRIED:
            for event_ref in family.get_event_ref_list():
                event = db.get_event_from_handle(event_ref.ref)
                if (event.get_type() == EventType.MARRIAGE and
                        (event_ref.get_role() == EventRoleType.FAMILY or
                         event_ref.get_role() == EventRoleType.PRIMARY)):
                    marriages.append(event_ref.ref)
    if marriages:
        return (marriages)
    return None

def get_or_create_all_place(event,placename):
    '''
    Create Place for Events or get an existing one based on the name
    '''
    try:
        pl = event.get_place_handle()
    except:
        place = Place()
        return(place)

    if pl:
        try:
            place = db.get_place_from_handle(pl)
            if args.verbosity >= 2:
                print("Reuse Place from Event:", placename)
        except:
            place = Place()
    else:
        keep = None
        # Check whether our place already exists
        for handle in db.get_place_handles():
            pl = db.get_place_from_handle(handle)
            explace = pl.get_name().value
            if args.verbosity >= 3:
                print("DEBUG: search for "+str(placename)+" in "+str(explace))
            if str(explace) == str(placename):
                keep = pl
                break
        if keep == None:
            if args.verbosity >= 2:
                print("Create Place:", placename)
            place = Place()
        else:
            if args.verbosity >= 2:
                print("Reuse existing Place:", placename)
            place = keep
    return(place)

def get_or_create_all_event(obj,gobj,attr,tran):
    '''
    Create Birth and Death Events for a person 
    and Marriage Events for a family or get an existing one
    obj is GPerson or GFamily
    gobj is a gramps object Person or Family
    '''
    
    event = None
    # Manages name indirection for person
    if gobj.__class__.__name__ == 'Person':
        role = EventRoleType.PRIMARY
        func = getattr(gobj,'get_'+attr+'_ref')
        reffunc = func()
        if reffunc:
            event = db.get_event_from_handle(reffunc.ref)
            if args.verbosity >= 2:
                print("Existing "+attr+" Event")
    elif gobj.__class__.__name__ == 'Family':
        role = EventRoleType.FAMILY
        if attr == 'marriage':
            marev = None
            for event_ref in gobj.get_event_ref_list():
                event = db.get_event_from_handle(event_ref.ref)
                if (event.get_type() == EventType.MARRIAGE and
                        (event_ref.get_role() == EventRoleType.FAMILY or
                         event_ref.get_role() == EventRoleType.PRIMARY)):
                    marev = event
            if marev:
                event = marev
                if args.verbosity >= 2:
                    print("Existing "+attr+" Event")
    else:
        print("ERROR: Unable to handle class %s in get_or_create_all_event"%(gobj.__class__.__name__))
                
    if event is None:
        event = Event()
        uptype = getattr(EventType,attr.upper())
        event.set_type(EventType(uptype))
        event.set_description('Imported from Geaneanet')
        db.add_event(event,tran)
        
        eventref = EventRef()
        eventref.set_role(role)
        eventref.set_reference_handle(event.get_handle())
        if gobj.__class__.__name__ == 'Person':
            func = getattr(gobj,'set_'+attr+'_ref')
            reffunc = func(eventref)
            db.commit_person(gobj,tran)
        elif gobj.__class__.__name__ == 'Family':
            eventref.set_role(EventRoleType.FAMILY)
            gobj.add_event_ref(eventref)
            if attr == 'marriage':
                gobj.set_relationship(FamilyRelType(FamilyRelType.MARRIED))
            db.commit_family(gobj,tran)
        if args.verbosity >= 2:
            print("Creating "+attr+" ("+str(uptype)+") Event")

    if obj.__dict__[attr] \
        or obj.__dict__[attr+'place'] \
        or obj.__dict__[attr+'placecode'] :
        # TODO: Here we create a new date each time there is a date in object
        date = Date()
        if obj.__dict__[attr]:
            if obj.__dict__[attr][:1] == 'ca':
                mod = Date.MOD_ABOUT 
            elif obj.__dict__[attr][:1] == 'av':
                mod = Date.MOD_BEFORE 
            elif obj.__dict__[attr][:1] == 'ap':
                mod = Date.MOD_AFTER 
            else:
                mod = Date.MOD_NONE 
            # ISO string, put in a tuple, reversed
            tab = obj.__dict__[attr].split('-')
            date.set_yr_mon_day(int(tab[0]),int(tab[1]),int(tab[2]))
        if args.verbosity >= 2:
            print("Update "+attr+" Date to "+obj.__dict__[attr])
        event.set_date_object(date)
        db.commit_event(event,tran)

        if obj.__dict__[attr+'place'] \
            or obj.__dict__[attr+'placecode'] :
            if obj.__dict__[attr+'place']:
                placename = obj.__dict__[attr+'place']
            else:
                placename = ""
            place = obj.get_or_create_place(event,placename)
            # TODO: Here we overwrite any existing value.
            place.set_name(PlaceName(value=placename))
            if obj.__dict__[attr+'placecode']:
                place.set_code(obj.__dict__[attr+'placecode'])
            db.add_place(place,tran)
            event.set_place_handle(place.get_handle())
            db.commit_event(event,tran)

    db.commit_event(event,tran)
    return

class Geneanet(Gramplet):
    '''
    Gramplet to import Geneanet persons into Gramps
    '''

    def init(sefl):
        self.gui.WIDGET = self.build_gui()
        self.gui.get_container_widget().remove(self.gui.textview)
        self.gui.get_container_widget().add(self.gui.WIDGET)
        self.gui.WIDGET.show()

    def build_gui(self):
        """
        Build the GUI interface.
        """
        tip = _('Double-click on a row to take the selected person as starting point.')
        self.set_tooltip(tip)
        self.view = Gtk.TreeView()
        titles = [(_('Name'), 0, 230),
                  (_('Birth'), 2, 100),
                  ('', NOSORT, 1),
                  ('', NOSORT, 1), # tooltip
                  ('', NOSORT, 100)] # handle
        self.model = ListModel(self.view, titles, list_mode="tree",
                               event_func=self.cb_double_click)
        return self.view

class GFamily():
    '''
    Family as seen by Gramps
    '''
    def __init__(self,gp0,gp1):
        if args.verbosity >= 1:
            print("Creating Family: "+gp0.lastname+" - "+gp1.lastname)
        self.marriage = ""
        self.marriageplace = ""
        self.marriageplacecode = ""
        self.children = []
        self.url = gp0.url
        if self.url == "":
            self.url = gp1.url

        # TODO: do these people already form a family, supposing not for now
        self.family = Family()
        with DbTxn("Geneanet import", db) as tran:
            db.add_family(self.family,tran)

            try:
                grampsp0 = db.get_person_from_gramps_id(gp0.gid)
            except:
                if args.verbosity >= 2:
                    print('No father for this family')
                grampsp0 = None

            if grampsp0:
                try:
                    self.family.set_father_handle(grampsp0.get_handle())
                except:
                    if args.verbosity >= 2:
                        print("Can't affect father to the family")

                db.commit_family(self.family,tran)
                grampsp0.add_family_handle(self.family.get_handle())
                db.commit_person(grampsp0,tran)

            try:
                grampsp1 = db.get_person_from_gramps_id(gp1.gid)
            except:
                if args.verbosity >= 2:
                    print('No mother for this family')
                grampsp1 = None

            if grampsp1:
                try:
                    self.family.set_mother_handle(grampsp1.get_handle())
                except:
                    if args.verbosity >= 2:
                        print("Can't affect mother to the family")

                db.commit_family(self.family,tran)
                grampsp1.add_family_handle(self.family.get_handle())
                db.commit_person(grampsp1,tran)

            # Now celebrate the marriage !
            # We need to find first the right spouse
            idx = 0
            for sr in gp0.spouseref:
                if args.verbosity >= 3:
                    print('Comparing sr %s to %s (idx: %d)'%(sr,gp1.url,idx))
                if sr == gp1.url:
                    break
                idx = idx + 1
            if idx < len(gp0.spouseref):
                # We found one
                self.marriage = gp0.marriage[idx]
                self.marriageplace = gp0.marriageplace[idx]
                self.marriageplacecode = gp0.marriageplacecode[idx]
                if args.verbosity >= 2:
                    print('Marriage found the %s at %s (%s)'%(self.marriage,self.marriageplace,self.marriageplacecode))
            else:
                if args.verbosity >= 2:
                    print('No marriage found')

            self.get_or_create_event(self.family,'marriage',tran)

    def add_child(self,child):
        if args.verbosity >= 1:
            print("Adding Child : "+child.firstname+" "+child.lastname)
        childref = ChildRef()
        try:
            grampsp = db.get_person_from_gramps_id(child.gid)
        except:
            if args.verbosity >= 2:
                print('No child for this family')
            grampsp = None
        if grampsp:
            try:
                childref.set_reference_handle(grampsp.get_handle())
            except:
                if args.verbosity >= 2:
                    print('No handle for this child')
            self.family.add_child_ref(childref)
            with DbTxn("Geneanet import", db) as tran:
                db.commit_family(self.family,tran)
                grampsp.add_parent_family_handle(self.family.get_handle())
                db.commit_person(grampsp,tran)
        
    def get_or_create_event(self,obj,attr,tran):
        '''
        Create Marriage Events for this family or get an existing one
        '''
        get_or_create_all_event(self,obj,attr,tran)
        return

    def get_or_create_place(self,event,placename):
        '''
        Create Place for Events or get an existing one based on the name
        '''
        return(get_or_create_all_place(event,placename))
        

class GPerson():
    '''
    Generic Person common between Gramps and Geneanet
    '''
    def __init__(self,level):
        if args.verbosity >= 3:
            print("Initialize Person")
        self.level = level
        self.firstname = ""
        self.lastname = ""
        self.sex = 'I'
        self.birth = None
        self.birthplace = None
        self.birthplacecode = None
        self.death = None
        self.deathplace = None
        self.deathplacecode = None
        self.gid = None
        self.url = ""
        self.family = []
        self.spouseref = []
        self.marriage = []
        self.marriageplace = []
        self.marriageplacecode = []
        self.childref = []
        self.fref = ""
        self.mref = ""
        # Father and Mother id in gramps
        self.fgid = None
        self.mgid = None

    def __smartcopy(self,p,attr):
        '''
        Smart Copying an attribute from p into self
        '''
        if args.verbosity >= 3:
            print("Smart Copying Attributes",attr)

        # By default do not copy
        scopy = False

        # Find the case where copy is to be done
        # Nothing yet
        if not self.__dict__[attr]:
            scopy = True

        # Force the copy
        if self.__dict__[attr] != p.__dict__[attr] and args.force:
            scopy = True

        # Improve sex if we can
        if attr == 'sex' and self.__dict__[attr] == 'I':
            scopy = True

        if scopy:
            self.__dict__[attr] = p.__dict__[attr]
        else:
            if args.verbosity >= 3:
                print("Not Copying Person attribute (%s, value %s) onto %s"%(attr, self.__dict__[attr],p.__dict__[attr]))

    def smartcopy(self,p):
        '''
        Smart Copying p into self
        '''
        if args.verbosity >= 2:
            print("Smart Copying Person")
        self.__smartcopy(p,"firstname")
        self.__smartcopy(p,"lastname")
        self.__smartcopy(p,"sex")
        self.__smartcopy(p,"url")
        self.__smartcopy(p,"birth")
        self.__smartcopy(p,"birthplace")
        self.__smartcopy(p,"birthplacecode")
        self.__smartcopy(p,"death")
        self.__smartcopy(p,"deathplace")
        self.__smartcopy(p,"deathplacecode")
        self.__smartcopy(p,"marriage")
        self.__smartcopy(p,"marriageplace")
        self.__smartcopy(p,"marriageplacecode")
        self.spouseref = p.spouseref
        self.childref = p.childref
        self.mref = p.mref
        self.fref = p.fref
        # Useful ?
        #self.family = p.family

    def from_geneanet(self,purl):
        ''' Use XPath to retrieve the details of a person
        Used example from https://gist.github.com/IanHopkinson/ad45831a2fb73f537a79
        and doc from https://www.w3schools.com/xml/xpath_axes.asp
        and https://docs.python-guide.org/scenarios/scrape/

        lxml can return _ElementUnicodeResult instead of str so cast
        '''

        # Needed as Geneanet returned relative links
        ROOTURL = 'https://gw.geneanet.org/'
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}


        if args.verbosity >= 3:
            print("Purl:",purl)
        if not purl:
            return()
        try:
            if args.verbosity >= 1:
                print('-----------------------------------------------------------')
                print("Page considered:",purl)
            page = requests.get(purl)
            if args.verbosity >= 3:
                print(_("Return code:"),page.status_code)
        except:
            print("We failed to reach the server at",purl)
        else:
            if page.ok:
                try:
                    tree = html.fromstring(page.content)
                except:
                    print(_("Unable to perform HTML analysis"))
    
                self.url = purl
                try:
                    # Should return F or H
                    sex = tree.xpath('//div[@id="person-title"]//img/attribute::alt')
                    self.sex = sex[0]
                except:
                    self.sex = 'I'
                try:
                    name = tree.xpath('//div[@id="person-title"]//a/text()')
                    self.firstname = str(name[0])
                    self.lastname = str(name[1])
                except:
                    self.firstname = ""
                    self.lastname = ""
                if args.verbosity >= 1:
                    print('==> GENEANET Name (L%d): %s %s'%(self.level,self.firstname,self.lastname))
                if args.verbosity >= 2:
                    print('Sex:', self.sex)
                try:
                    birth = tree.xpath('//li[contains(., "Né")]/text()')
                except:
                    birth = [""]
                try:
                    death = tree.xpath('//li[contains(., "Décédé")]/text()')
                except:
                    death = [""]
                try:
                    # sometime parents are using circle, somtimes disc !
                    parents = tree.xpath('//ul[not(descendant-or-self::*[@class="fiche_union"])]//li[@style="vertical-align:middle;list-style-type:disc" or @style="vertical-align:middle;list-style-type:circle"]')
                except:
                    parents = []
                try:
                    spouses = tree.xpath('//ul[@class="fiche_union"]/li')
                except:
                    spouses = []
                try:
                    ld = convert_date(birth[0].split('-')[0].split()[1:])
                    if args.verbosity >= 2:
                        print('Birth:', ld)
                    self.birth = ld
                except:
                    self.birth = ""
                try:
                    self.birthplace = str(birth[0].split('-')[1].split(',')[0].strip())
                    if args.verbosity >= 2:
                        print('Birth place:', self.birthplace)
                except:
                    self.birthplace = ""
                try:
                    self.birthplacecode = str(birth[0].split('-')[1].split(',')[1]).strip()
                    if args.verbosity >= 2:
                        print('Birth place code:', self.birthplacecode)
                except:
                    self.birthplacecode = ""
                try:
                    ld = convert_date(death[0].split('-')[0].split()[1:])
                    if args.verbosity >= 2:
                        print('Death:', ld)
                    self.death = ld
                except:
                    self.death = ""
                try:
                    self.deathplace = str(death[0].split('-')[1].split(',')[0]).strip()
                    if args.verbosity >= 2:
                        print('Death place:', self.deathplace)
                except:
                    self.deathplace = ""
                try:
                    self.deathplacecode = str(death[0].split('-')[1].split(',')[1]).strip()
                    if args.verbosity >= 2:
                        print('Death place code:', self.deathplacecode)
                except:
                    self.deathplacecode = ""

                s = 0
                sname = []
                sref = []
                marriage = []
                children = []
                for spouse in spouses:
                    try:
                        sname.append(str(spouse.xpath('a/text()')[0]))
                        if args.verbosity >= 2:
                            print('Spouse name:', sname[s])
                    except:
                        sname.append("")

                    try:
                        sref.append(str(spouse.xpath('a/attribute::href')[0]))
                        if args.verbosity >= 2:
                            print('Spouse ref:', ROOTURL+sref[s])
                    except:
                        sref.append("")
                    self.spouseref.append(ROOTURL+sref[s])

                    try:
                        marriage.append(str(spouse.xpath('em/text()')[0]))
                    except: 
                        marriage.append("")
                    try:
                        ld = convert_date(marriage[s].split(',')[0].split()[1:])
                        if args.verbosity >= 2:
                            print('Married:', ld)
                        self.marriage.append(ld)
                    except:
                        self.marriage.append("")
                    try:
                        self.marriageplace.append(str(marriage[s].split(',')[1][1:]))
                        if args.verbosity >= 2:
                            print('Married place:', self.marriageplace[s])
                    except:
                        self.marriageplace.append("")
                    try:
                        self.marriageplacecode.append(str(marriage[s].split(',')[2][1:]))
                        if args.verbosity >= 2:
                            print('Married place code:', self.marriageplacecode[s])
                    except:
                        self.marriageplacecode.append("")
    
                    children.append(tree.xpath('//ul[@class="fiche_union"]/li/ul/li'))
                    cnum = 0
                    clist = []
                    for c in children:
                        try:
                            cname = c.xpath('a/text()')[0]
                            if args.verbosity >= 2:
                                print('Child %d name: %s'%(cnum,cname))
                        except:
                            cname = ""
                        try:
                            cref = c.xpath('a/attribute::href')[0]
                            if args.verbosity >= 2:
                                print('Child %d ref: %s'%(cnum,ROOTURL+cref))
                        except:
                            cref = ""
                        clist.append(ROOTURL+str(cref))
                        cnum = cnum + 1
                    self.childref.append(clist)
                    if args.verbosity >= 2:
                        for c in self.childref[s]:
                            print('Child:', c)
                    s = s + 1
                    # End spouse loop
    
                self.fref = ""
                self.mref = ""
                self.pref = []
                for p in parents:
                    if args.verbosity >= 3:
                        print(p.xpath('text()'))
                    if p.xpath('text()')[0] == '\n':
                        try:
                            pname = p.xpath('a/text()')[0]
                            print('Parent name: %s'%(pname))
                        except:
                            pname = ""
                            # if pname is ? ? then go to next one
                        try:
                            pref = p.xpath('a/attribute::href')[0]
                            print('Parent ref:', ROOTURL+pref)
                        except:
                            pref = ""
                        self.pref.append(ROOTURL+str(pref))
                try:
                    self.fref = self.pref[0]
                except:
                    self.fref = ""
                try:
                    self.mref = self.pref[1]
                except:
                    self.mref = ""
                if args.verbosity >= 2:
                    print('-----------------------------------------------------------')
    
            else:
                print(_("We failed to be ok with the server"))


    def get_or_create_place(self,event,placename):
        '''
        Create Place for Events or get an existing one based on the name
        '''
        return(get_or_create_all_place(event,placename))
        
    def get_or_create_event(self,obj,attr,tran):
        '''
        Create Birth and Death Events for this person or get an existing one
        '''
        get_or_create_all_event(self,obj,attr,tran)
        return

    def validate(self,p):
        '''
        Validate the GPerson attributes 
        and use them to enrich or create a Gramps Person
        using data from the Genanet p person
        '''

        self.smartcopy(p)
        with DbTxn("Geneanet import", db) as tran:
            db.disable_signals()
            grampsp = db.get_person_from_gramps_id(self.gid)
            if grampsp:
                if args.verbosity >= 2:
                    print("Existing Gramps Person:", self.gid)
            else:
                # Create a new Person in Gramps
                grampsp = Person()
                db.add_person(grampsp,tran)
                self.gid = grampsp.gramps_id
                if args.verbosity >= 2:
                    print("Create new Gramps Person: "+self.gid+' ('+self.firstname+' '+self.lastname+')')

            if self.sex == 'H':
                grampsp.set_gender(Person.MALE)
            elif self.sex == 'F':
                grampsp.set_gender(Person.FEMALE)
            else:
                grampsp.set_gender(Person.UNKNOWN)
            n = Name()
            n.set_type(NameType(NameType.BIRTH))
            n.set_first_name(self.firstname)
            s = n.get_primary_surname()
            s.set_surname(self.lastname)
            grampsp.set_primary_name(n)
    
            # We need to create events for Birth and Death
            for ev in ['birth', 'death']:
                self.get_or_create_event(grampsp,ev,tran)

            # Store the importation place as an Internet note
            if self.url != "":
                found = False
                for u in grampsp.get_url_list():
                    if u.get_type() == UrlType.WEB_HOME
                    and u.get_path() == self.url:
                        found = True
                if not found:
                    url = Url()
                    url.set_description("Imported from Geneanet")
                    url.set_type(UrlType.WEB_HOME)
                    url.set_path(self.url)
                    grampsp.add_url(url)
 
            db.commit_person(grampsp,tran)
            db.enable_signals()
            db.request_rebuild()
 
    def from_gramps(self,gid):

        GENDER = ['F', 'H', 'I']

        self.gid = gid
        if gid == None:
            return
        try:
            grampsp = db.get_person_from_gramps_id(gid)
            if args.verbosity >= 3:
                print("Person object:", grampsp)
            if grampsp.gender:
                self.sex = GENDER[grampsp.gender]
                if args.verbosity >= 1:
                    print("Gender:",GENDER[grampsp.gender])
            name = grampsp.primary_name.get_name().split(', ')
            if name[0]:
                self.firstname = name[1]
            else:
                self.lastname = ""
            if name[1]:
                self.lastname = name[0]
            else:
                self.firstname = ""
            if args.verbosity >= 1:
                print("===> GRAMPS Name: %s %s"%(self.firstname,self.lastname))
                print("Gramps Id: %s"%(gid))
        except:
            if args.verbosity >= 2:
                print(_("WARNING: Unable to retrieve id %s from the gramps db %s")%(gid,gname))
            return

        try:
            bd = get_gramps_date(grampsp,BIRTH,db)
            if bd:
                if args.verbosity >= 1:
                    print("Birth:",bd)
                self.birth = bd
            else:
                if args.verbosity >= 1:
                    print("No Birth date")
        except:
            if args.verbosity >= 1:
                print(_("WARNING: Unable to retrieve birth date for id %s")%(gid))

        try:
            dd = get_gramps_date(grampsp,DEATH,db)
            if dd:
                if args.verbosity >= 1:
                    print("Death:",dd)
                self.death = dd
            else:
                if args.verbosity >= 1:
                    print("No Death date")
        except:
            if args.verbosity >= 1:
                print(_("WARNING: Unable to retrieve death date for id %s")%(gid))
        
        try:
            fh = grampsp.get_main_parents_family_handle()
            if fh:
                if args.verbosity >= 3:
                    print("Family:",fh)
                fam = db.get_family_from_handle(fh)
                if fam:
                    if args.verbosity >= 1:
                        print("Family:",fam)
                # find father from a family
                fh = fam.get_father_handle()
                if fh:
                    if args.verbosity >= 3:
                        print("Father H:",fh)
                    father = db.get_person_from_handle(fh)
                    if father:
                        if args.verbosity >= 1:
                            print("Father name:",father.primary_name.get_name())
                        self.fgid = father.gramps_id
                mh = fam.get_mother_handle()
                if mh:
                    if args.verbosity >= 3:
                        print("Mother H:",mh)
                    mother = db.get_person_from_handle(mh)
                    if mother:
                        if args.verbosity >= 1:
                            print("Mother name:",mother.primary_name.get_name())
                        self.mgid = mother.gramps_id
        except:
            if args.verbosity >= 1:
                print(_("WARNING: Unable to retrieve family for id %s")%(gid))

    def recurse_parents(self,level):
        '''
        analyze the parents of the person passed in parameter recursively
        '''
        # Recurse while we have parents urls and level not reached
        if level <= args.level and (self.fref != "" or self.mref != ""):
            level = level + 1
            time.sleep(TIMEOUT)
            gp0 = geneanet_to_gramps(level,self.fgid,self.fref)
            gp1 = geneanet_to_gramps(level,self.mgid,self.mref)

            # In case there are errors between father and mother, check and fix
            if gp0.sex == 'H':
                father = gp0
                mother = gp1
            else:
                father = gp1
                mother = gp0

            if args.verbosity >= 2:
                print("=> Recursing on the father of "+self.firstname+" "+self.lastname+' : '+father.firstname+' '+father.lastname)
            father.recurse_parents(level)
            if args.verbosity >= 2:
                print("=> End of recursion on the father of "+self.firstname+" "+self.lastname+' : '+father.firstname+' '+father.lastname)
            time.sleep(TIMEOUT)
            if args.verbosity >= 2:
                print("=> Recursing on the mother of "+self.firstname+" "+self.lastname+' : '+mother.firstname+' '+mother.lastname)
            mother.recurse_parents(level)
            if args.verbosity >= 2:
                print("=> End of recursing on the mother of "+self.firstname+" "+self.lastname+' : '+mother.firstname+' '+mother.lastname)
            if args.verbosity >= 2:
                print("=> Initialize Family of "+self.firstname+" "+self.lastname)
            f = GFamily(father,mother)
            f.add_child(self)
        if level > args.level:
            if args.verbosity >= 1:
                print("Stopping exploration as we reached level "+str(level))

    def recurse_children(level,gp):
        '''
        analyze the children of the person passed in parameter recursively
        '''
        # TODO: probably need the spouse as param
        # Recurse while we have parents urls and level not reached
        if level <= args.level and (gp.fref != "" or gp.mref != ""):
            level = level + 1
            time.sleep(TIMEOUT)

def import_data(database, filename, user):

    global callback

    try:
        g = GeneanetParser(database)
    except IOError as msg:
        user.notify_error(_("%s could not be opened\n") % filename,str(msg))
        return

    try:
        #status = g.find_geneanet_person(purl)
        pass
    except IOError as msg:
        errmsg = _("%s could not be opened\n") % filename
        user.notify_error(errmsg,str(msg))
        return
    return ImportInfo({_("Results"): _("done")})

def geneanet_to_gramps(level, gid, url):
    '''
    Function to create a person from Geneanet into gramps
    '''
    # Create the Person coming from Gramps
    gp = GPerson(level)
    gp.from_gramps(gid)

    # Create the Person coming from Geneanet
    p = GPerson(level)
    p.from_geneanet(url)

    # Check we point to the same person
    if gid != None:
        if (gp.firstname != p.firstname or gp.lastname != p.lastname) and (not args.force):
            print("Gramps   person: %s %s"%(gp.firstname,gp.lastname))
            print("Geneanet person: %s %s"%(p.firstname,p.lastname))
            db.close()
            sys.exit("Do not continue without force")

        if (gp.birth != p.birth or gp.death != p.death) and (not args.force):
            print("Gramps   person birth/death: %s / %s"%(gp.birth,gp.death))
            print("Geneanet person birth/death: %s / %s"%(p.birth,p.death))
            db.close()
            sys.exit("Do not continue without force")

    # Copy from Geneanet into Gramps and commit
    gp.validate(p)
    return(gp)

def main():

    # Global vars
    global args
    global db
    global gname

    LEVEL = 0

    parser = argparse.ArgumentParser(description="Import Geneanet subtrees into Gramps")
    parser.add_argument("-v", "--verbosity", action="count", default=0, help="Increase verbosity")
    parser.add_argument("-a", "--ascendants", default=False, action='store_true', help="Includes ascendants (off by default)")
    parser.add_argument("-d", "--descendants", default=False, action='store_true', help="Includes descendants (off by default)")
    parser.add_argument("-s", "--spouse", default=False, action='store_true', help="Includes spouse (off by default)")
    parser.add_argument("-l", "--level", default=1, type=int, help="Number of level to explore (1 by default)")
    parser.add_argument("-g", "--grampsfile", type=str, help="Name of the Gramps database")
    parser.add_argument("-i", "--id", type=str, help="ID of the person to start from in Gramps")
    parser.add_argument("-f", "--force", default=False, action='store_true', help="Force processing")
    parser.add_argument("searchedperson", type=str, nargs='?', help="Url of the person to search in Geneanet")
    args = parser.parse_args()

    if args.verbosity >= 1:
        print("LEVEL:",LEVEL)
    if args.searchedperson == None:
        #purl = 'https://gw.geneanet.org/agnesy?lang=fr&pz=hugo+mathis&nz=renard&p=marie+sebastienne&n=helgouach'
        purl = 'https://gw.geneanet.org/agnesy?lang=fr&n=queffelec&oc=17&p=marie+anne'
    else:
        purl = args.searchedperson

    gname = args.grampsfile
	
    # TODO: to a backup before opening
    if gname == None:
        #gname = "Test import"
        # To be searched in ~/.gramps/recent-files-gramps.xml
        gname = "/users/bruno/.gramps/grampsdb/5ec17554"
    try:
        dbstate = DbState()
        climanager = CLIManager(dbstate, True, None)
        climanager.open_activate(gname)
        db = dbstate.db
    except:
        ErrorDialog(_("Opening the '%s' database") % gname,
                    _("An attempt to convert the database failed. "
                      "Perhaps it needs updating."), parent=self.top)
        sys.exit()
    	
    gid = args.id
    if gid == None:
        gid = "0000"
    gid = "I"+gid
    
    ids = db.get_person_gramps_ids()
    for i in ids:
        if args.verbosity >= 1:
            print(i)
    
    if args.verbosity >= 1 and args.force:
        print("WARNING: Force mode activated")
        time.sleep(TIMEOUT)
    
    # Create the first Person 
    gp = geneanet_to_gramps(LEVEL,gid,purl)
    
    if args.ascendants:
       gp.recurse_parents(LEVEL)
    
    LEVEL = 0
    if args.descendants:
        time.sleep(TIMEOUT)
    
    db.close()
    sys.exit(0)
    

if __name__ == '__main__':
    main()

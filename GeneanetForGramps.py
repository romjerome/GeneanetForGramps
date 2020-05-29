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
import re

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

class GBase:

    def __init__(self):
        pass

    def _smartcopy(self,attr):
        '''
        Smart Copying an attribute from geneanet (g_ attrs) into attr
        Works for GPerson and GFamily
        '''
        if args.verbosity >= 3:
            print("Smart Copying Attributes",attr)

        # By default do not copy as Gramps is the master reference
        scopy = False

        # Find the case where copy is to be done
        # Nothing yet
        if not self.__dict__[attr]:
            scopy = True

        # Empty field
        if self.__dict__[attr] and self.__dict__[attr] == "" and self.__dict__['g_'+attr] != "":
            scopy = True

        # Force the copy
        if self.__dict__[attr] != self.__dict__['g_'+attr] and args.force:
            scopy = True

        # Managing sex, Gramps is always right except when unknown
        # Warn on conflict
        if attr == 'sex' and self.__dict__[attr] == 'I' and self.__dict__['g_'+attr] != 'I':
            scopy = True
            if (self.__dict__[attr] == 'F' and self.__dict__['g_'+attr] == 'H') \
            or (self.__dict__[attr] == 'H' and self.__dict__['g_'+attr] == 'F'):
                if args.verbosity >= 1:
                    print("Gender conflict between Geneanet and Gramps, keeping Gramps value")
                scopy = False

        if attr == 'lastname' and self.__dict__[attr] != self.__dict__['g_'+attr]:
            if args.verbosity >= 1:
                print("Lastname conflict between Geneanet and Gramps, keeping Gramps value")
        if attr == 'lastname' and self.__dict__[attr] == "":
            scopy = True

        if attr == 'firstname' and self.__dict__[attr] != self.__dict__['g_'+attr]:
            if args.verbosity >= 1:
                print("Firstname conflict between Geneanet and Gramps, keeping Gramps value")
        if attr == 'firstname' and self.__dict__[attr] == "":
            scopy = True

        # Copy only if code is more precise
        match = re.search(r'code$', attr)
        if match:
            if not self.__dict__[attr]:
                scopy = True
            else:
                if not self.__dict__['g_'+attr]:
                    scopy = False
                else:
                    if int(self.__dict__[attr]) < int(self.__dict__['g_'+attr]):
                        scopy = True

        # Copy only if date is more precise
        match = re.search(r'date$', attr)
        if match:
            if not self.__dict__[attr]:
                scopy = True
            else:
                if not self.__dict__['g_'+attr]:
                    scopy = False
                else:
                    if self.__dict__[attr] < self.__dict__['g_'+attr]:
                        scopy = True

        if scopy:
            if args.verbosity >= 2:
                print("Copying Person attribute %s (former value %s newer value %s)"%(attr, self.__dict__[attr],self.__dict__['g_'+attr]))

            self.__dict__[attr] = self.__dict__['g_'+attr]
        else:
            if args.verbosity >= 3:
                print("Not Copying Person attribute (%s, value %s) onto %s"%(attr, self.__dict__[attr],self.__dict__['g_'+attr]))


    def get_or_create_place(self,event,placename):
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
                    print("Reuse Place from Event:", place.get_name().value)
            except:
                place = Place()
        else:
            if placename == None:
                place = Place()
                return(place)
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
    
    def get_or_create_event(self,gobj,attr,tran):
        '''
        Create Birth and Death Events for a person 
        and Marriage Events for a family or get an existing one
        self is GPerson or GFamily
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
                db.commit_event(event,tran)
                db.commit_person(gobj,tran)
            elif gobj.__class__.__name__ == 'Family':
                eventref.set_role(EventRoleType.FAMILY)
                gobj.add_event_ref(eventref)
                if attr == 'marriage':
                    gobj.set_relationship(FamilyRelType(FamilyRelType.MARRIED))
                db.commit_event(event,tran)
                db.commit_family(gobj,tran)
            if args.verbosity >= 2:
                print("Creating "+attr+" ("+str(uptype)+") Event")
    
        if self.__dict__[attr+'date'] \
            or self.__dict__[attr+'place'] \
            or self.__dict__[attr+'placecode'] :
            # Get or create the event date
            date = event.get_date_object()
            if self.__dict__[attr+'date']:
                if self.__dict__[attr+'date'][:1] == 'ca':
                    mod = Date.MOD_ABOUT 
                if self.__dict__[attr+'date'][:1] == 've':
                    mod = Date.MOD_ABOUT 
                elif self.__dict__[attr+'date'][:1] == 'av':
                    mod = Date.MOD_BEFORE 
                elif self.__dict__[attr+'date'][:1] == 'ap':
                    mod = Date.MOD_AFTER 
                else:
                    mod = Date.MOD_NONE 
                # ISO string, put in a tuple, reversed
                tab = self.__dict__[attr+'date'].split('-')
                date.set_yr_mon_day(int(tab[0]),int(tab[1]),int(tab[2]))
            if args.verbosity >= 2 and self.__dict__[attr+'date']:
                print("Update "+attr+" Date to "+self.__dict__[attr+'date'])
            event.set_date_object(date)
            db.commit_event(event,tran)
    
            if self.__dict__[attr+'place'] \
                or self.__dict__[attr+'placecode'] :
                if self.__dict__[attr+'place']:
                    placename = self.__dict__[attr+'place']
                else:
                    placename = ""
                place = self.get_or_create_place(event,placename)
                # TODO: Here we overwrite any existing value. 
                # Check whether that can be a problem
                place.set_name(PlaceName(value=placename))
                if self.__dict__[attr+'placecode']:
                    place.set_code(self.__dict__[attr+'placecode'])
                db.add_place(place,tran)
                event.set_place_handle(place.get_handle())
                db.commit_event(event,tran)
    
        db.commit_event(event,tran)
        return

    def get_gramps_date(self,evttype):
        '''
        Give back the date of the event related to the Person or Family
        '''
    
        if args.verbosity >= 3:
            print("EventType: %d"%(evttype))
    
        if not self:
            return
    
        if evttype == EventType.BIRTH:
            ref = self.grampsp.get_birth_ref()
        elif evttype == EventType.DEATH:
            ref = self.grampsp.get_death_ref()
        elif evttype == EventType.MARRIAGE:
            for eventref in self.grampsp.get_event_ref_list():
                event = db.get_event_from_handle(eventref.ref)
                if (event.get_type() == EventType.MARRIAGE
                    and (eventref.get_role() == EventRoleType.FAMILY
                    or eventref.get_role() == EventRoleType.PRIMARY)):
                        break
            ref = eventref
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


class GFamily(GBase):
    '''
    Family as seen by Gramps
    gp0 is the father GPerson
    gp1 is the mother GPerson
    '''
    def __init__(self):
        self.marriagedate = None
        self.marriageplace = None
        self.marriageplacecode = None
        self.url = ""
        self.children = []
        self.father = None
        self.mother = None
        self.family = None

    def create(self,gp0,gp1):
        if args.verbosity >= 1:
            print("Creating Family: "+gp0.lastname+" - "+gp1.lastname)
        self.url = gp0.url
        if self.url == "":
            self.url = gp1.url
        self.father = gp0
        self.mother = gp1

        # Do these people already form a family
        for fid in db.get_family_gramps_ids():
            f = db.get_family_from_gramps_id(fid)
            fh = f.get_father_handle()
            # TODO: what about a family whose father is unknown
            if not fh: 
                if args.verbosity >= 1:
                    print("WARNING: no father for this family")
                    continue
            fo = db.get_person_from_handle(fh)
            mh = f.get_mother_handle()
            # TODO: what about a family whose mother is unknown
            if not mh:
                if args.verbosity >= 1:
                    print("WARNING: no mother for this family")
                    continue
            mo = db.get_person_from_handle(mh)
            if gp0.gid == fo.gramps_id and gp1.gid == mo.gramps_id:
                self.family = f
                self.marriagedate = f.get_gramps_date(MARRIAGE)
                for eventref in self.family.get_event_ref_list():
                    event = db.get_event_from_handle(eventref)
                    if (event.get_type() == EventType.MARRIAGE
                    and (eventref.get_role() == EventRoleType.FAMILY
                    or eventref.get_role() == EventRoleType.PRIMARY)):
                        place = get_or_create_place(event,None)
                        self.marriageplace = place.get_name().value
                        self.marriageplacecode = place.get_code()
                break
                if args.verbosity >= 2:
                    print("Found an existing family "+gp0stname+ " - "+gp1.lastname)

        with DbTxn("Geneanet import", db) as tran:
            # When it's not the case create the family
            if self.family == None:
                self.family = Family()
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

            # Now celebrate the marriage ! (if needed)
            # and moreover copy what comes from Geneanet if needed
            # We need to find first the right spouse
            idx = 0
            for sr in gp0.spouseref:
                if args.verbosity >= 3:
                    print('Comparing sr %s to %s (idx: %d)'%(sr,gp1.url,idx))
                if sr == gp1.url:
                    gp0.spouse.append(gp1)
                    gp0.family.append(self)
                    break
                else:
                    gp0.spouse.append(None)
                    gp0.family.append(None)
                idx = idx + 1
            if idx < len(gp0.spouseref):
                # We found one
                tmpf = GFamily()
                tmpf.marriagedate = gp0.marriagedate[idx]
                tmpf.marriageplace = gp0.marriageplace[idx]
                tmpf.marriageplacecode = gp0.marriageplacecode[idx]
                # TODO: doesn't work anymore
                self.smartcopy()
                # Cross reference spouses
            idx1 = 0
            for sr1 in gp1.spouseref:
                if sr1 == gp0.url:
                    gp1.spouse.append(gp0)
                    gp1.family.append(self)
                    break
                else:
                    gp1.spouse.append(None)
                    gp1.family.append(None)
                idx1 = idx1 + 1
            if idx1 < len(gp1.spouseref):
                # We found one
                tmpf = GFamily()
                tmpf.marriagedate = gp1.marriagedate[idx]
                tmpf.marriageplace = gp1.marriageplace[idx]
                tmpf.marriageplacecode = gp1.marriageplacecode[idx]
                # TODO: doesn't work anymore
                self.smartcopy()
            if self.marriagedate and self.marriageplace and self.marriageplacecode:
                if args.verbosity >= 2:
                    print('Marriage found the %s at %s (%s)'%(self.marriagedate,self.marriageplace,self.marriageplacecode))

            self.get_or_create_event(self.family,'marriage',tran)

    def smartcopy(self):
        '''
        Smart Copying GFamily
        '''
        if args.verbosity >= 2:
            print("Smart Copying Family")
        self._smartcopy("marriagedate")
        self._smartcopy("marriageplace")
        self._smartcopy("marriageplacecode")

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
        
class GPerson(GBase):
    '''
    Generic Person common between Gramps and Geneanet
    '''
    def __init__(self,level):
        if args.verbosity >= 3:
            print("Initialize Person")
        # Counter
        self.level = level
        # Gramps
        self.firstname = ""
        self.lastname = ""
        self.sex = 'I'
        self.birthdate = None
        self.birthplace = None
        self.birthplacecode = None
        self.deathdate = None
        self.deathplace = None
        self.deathplacecode = None
        self.gid = None
        self.grampsp = None
        # Father and Mother and Spouses GPersons
        self.father = None
        self.mother = None
        self.spouse = []
        # GFamilies
        self.family = []
        # Geneanet
        self.g_firstname = ""
        self.g_lastname = ""
        self.g_sex = 'I'
        self.g_birthdate = None
        self.g_birthplace = None
        self.g_birthplacecode = None
        self.g_deathdate = None
        self.g_deathplace = None
        self.g_deathplacecode = None
        self.url = ""
        self.spouseref = []
        self.fref = ""
        self.mref = ""
        self.marriagedate = []
        self.marriageplace = []
        self.marriageplacecode = []
        self.childref = []

    def smartcopy(self):
        '''
        Smart Copying GPerson
        '''
        if args.verbosity >= 2:
            print("Smart Copying Person",self.gid)
        self._smartcopy("firstname")
        self._smartcopy("lastname")
        self._smartcopy("sex")
        self._smartcopy("birthdate")
        self._smartcopy("birthplace")
        self._smartcopy("birthplacecode")
        self._smartcopy("deathdate")
        self._smartcopy("deathplace")
        self._smartcopy("deathplacecode")

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
                    self.g_sex = sex[0]
                except:
                    self.g_sex = 'I'
                try:
                    name = tree.xpath('//div[@id="person-title"]//a/text()')
                    self.g_firstname = str(name[0])
                    self.g_lastname = str(name[1])
                except:
                    self.g_firstname = ""
                    self.g_lastname = ""
                if args.verbosity >= 1:
                    print('==> GENEANET Name (L%d): %s %s'%(self.level,self.g_firstname,self.g_lastname))
                if args.verbosity >= 2:
                    print('Sex:', self.g_sex)
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
                    self.g_birthdate = ld
                except:
                    self.g_birthdate = None
                try:
                    self.g_birthplace = str(' '.join(birth[0].split('-')[1:]).split(',')[0].strip())
                    if args.verbosity >= 2:
                        print('Birth place:', self.g_birthplace)
                except:
                    self.g_birthplace = None
                try:
                    self.g_birthplacecode = str(' '.join(birth[0].split('-')[1:]).split(',')[1]).strip()
                    match = re.search(r'\d\d\d\d\d', self.g_birthplacecode)
                    if not match:
                        self.g_birthplacecode = None
                    else:
                        if args.verbosity >= 2:
                            print('Birth place code:', self.g_birthplacecode)
                except:
                    self.g_birthplacecode = None
                try:
                    ld = convert_date(death[0].split('-')[0].split()[1:])
                    if args.verbosity >= 2:
                        print('Death:', ld)
                    self.g_deathdate = ld
                except:
                    self.g_deathdate = None
                try:
                    self.g_deathplace = str(' '.join(death[0].split('-')[1:]).split(',')[0]).strip()
                    if args.verbosity >= 2:
                        print('Death place:', self.g_deathplace)
                except:
                    self.g_deathplace = None
                try:
                    self.g_deathplacecode = str(' '.join(death[0].split('-')[1:]).split(',')[1]).strip()
                    match = re.search(r'\d\d\d\d\d', self.g_deathplacecode)
                    if not match:
                        self.g_deathplacecode = None
                    else:
                        if args.verbosity >= 2:
                            print('Death place code:', self.g_deathplacecode)
                except:
                    self.g_deathplacecode = None

                s = 0
                sname = []
                sref = []
                marriage = []
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
                        marriage.append(None)
                    try:
                        ld = convert_date(marriage[s].split(',')[0].split()[1:])
                        if args.verbosity >= 2:
                            print('Married:', ld)
                        self.marriagedate.append(ld)
                    except:
                        self.marriagedate.append(None)
                    try:
                        self.marriageplace.append(str(marriage[s].split(',')[1][1:]))
                        if args.verbosity >= 2:
                            print('Married place:', self.marriageplace[s])
                    except:
                        self.marriageplace.append(None)
                    try:
                        marriageplacecode = str(marriage[s].split(',')[2][1:])
                        match = re.search(r'\d\d\d\d\d', marriageplacecode)
                        if not match:
                            self.marriageplacecode.append(None)
                        else:
                            if args.verbosity >= 2:
                                print('Married place code:', self.marriageplacecode[s])
                            self.marriageplacecode.append(marriageplacecode)
                    except:
                        self.marriageplacecode.append(None)
    
                    cnum = 0
                    clist = []
                    for c in spouse.xpath('ul/li'):
                        try:
                            cname = c.xpath('a/text()')[0]
                            if args.verbosity >= 2:
                                print('Child %d name: %s'%(cnum,cname))
                        except:
                            cname = None
                        try:
                            cref = ROOTURL+str(c.xpath('a/attribute::href')[0])
                            if args.verbosity >= 2:
                                print('Child %d ref: %s'%(cnum,cref))
                        except:
                            cref = None
                        clist.append(cref)
                        cnum = cnum + 1
                    self.childref.append(clist)
                    s = s + 1
                    # End spouse loop
    
                self.fref = ""
                self.mref = ""
                prefl = []
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
                        prefl.append(ROOTURL+str(pref))
                try:
                    self.fref = prefl[0]
                except:
                    self.fref = ""
                try:
                    self.mref = prefl[1]
                except:
                    self.mref = ""
                if args.verbosity >= 2:
                    print('-----------------------------------------------------------')
    
            else:
                print(_("We failed to be ok with the server"))


    def create_grampsp(self):
        '''
        Create a Person in Gramps and return it
        '''
        with DbTxn("Geneanet import", db) as tran:
            grampsp = Person()
            db.add_person(grampsp,tran)
            self.gid = grampsp.gramps_id
            self.grampsp = grampsp
            if args.verbosity >= 2:
                print("Create new Gramps Person: "+self.gid+' ('+self.firstname+' '+self.lastname+')')


    def find_grampsp(self):
        '''
        Find a Person in Gramps and return it
        '''
        p = None
        ids = db.get_person_gramps_ids()
        for i in ids:
            p = db.get_person_from_gramps_id(i)
            if p.get_primary_name().get_first_name() == self.firstname \
            and p.get_primary_name().get_surname() == self.lastname \
            and (p.get_gramps_date(EventType.BIRTH) == self.birthdate \
                or p.get_gramps_date(EventType.DEATH) == self.deathdate):
                if args.verbosity >= 2:
                    print("Found a Gramps Person: "+self.firstname+' '+self.lastname)
                self.gid = p.gramps_id
                return(p)
        return(None)

    def to_gramps(self):
        '''
        Push into Gramps the GPerson
        '''

        # Smart copy from Geneanet to Gramps inside GPerson
        self.smartcopy()

        with DbTxn("Geneanet import", db) as tran:
            db.disable_signals()
            grampsp = self.grampsp
            if not grampsp:
                if args.verbosity >= 2:
                    print("ERROR: Unable sync unknown Gramps Person")
                return

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
                    if u.get_type() == UrlType.WEB_HOME \
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
            # TODO: and the family and the parents ?
            #self.family.to_gramps()
            #self.father.to_gramps()
            #self.mother.to_gramps()
 
    def from_gramps(self,gid):

        GENDER = ['F', 'H', 'I']

        # If our gid was already setup and we didn't pass one
        if not gid and self.gid:
            gid = self.gid

        found = None
        try:
            found = db.get_person_from_gramps_id(gid)
            self.gid = gid
            self.grampsp = found
            if args.verbosity >= 2:
                print("Existing Gramps Person:", self.gid)
        except:
            if args.verbosity >= 1:
                print(_("WARNING: Unable to retrieve id %s from the gramps db %s")%(gid,gname))

        if not found:
            # If we don't know who this is, try to find it in Gramps 
            # This supposes that Geneanet data are already present in GPerson
            self.grampsp = self.find_grampsp()
            # And if we haven't found it, create it in gramps
            if self.grampsp == None:
                self.create_grampsp()

        if self.grampsp.gender:
            self.sex = GENDER[self.grampsp.gender]
            if args.verbosity >= 1:
                print("Gender:",self.sex)

        try:
            name = self.grampsp.primary_name.get_name().split(', ')
        except:
            name = [None, None]

        if name[0]:
            self.firstname = name[1]
        if name[1]:
            self.lastname = name[0]
        if args.verbosity >= 1:
            print("===> Gramps Name of %s: %s %s"%(gid,self.firstname,self.lastname))

        try:
            bd = self.get_gramps_date(EventType.BIRTH)
            if bd:
                if args.verbosity >= 1:
                    print("Birth:",bd)
                self.birthdate = bd
            else:
                if args.verbosity >= 1:
                    print("No Birth date")
        except:
            if args.verbosity >= 1:
                print(_("WARNING: Unable to retrieve birth date for id %s")%(gid))

        try:
            dd = self.get_gramps_date(EventType.DEATH)
            if dd:
                if args.verbosity >= 1:
                    print("Death:",dd)
                self.deathdate = dd
            else:
                if args.verbosity >= 1:
                    print("No Death date")
        except:
            if args.verbosity >= 1:
                print(_("WARNING: Unable to retrieve death date for id %s")%(gid))
        
        # TODO: Now this has to be changed
        try:
            fh = self.grampsp.get_main_parents_family_handle()
            if fh:
                if args.verbosity >= 3:
                    print("Family:",fh)
                fam = db.get_family_from_handle(fh)
                if fam:
                    if args.verbosity >= 1:
                        print("Family:",fam)
                    # TODO: create a GFamily with it and do a from_gramps on it
                    f = GFamily()
                    f.from_geneanet()
                    f.from_gramps()
                    self.family = f
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
                        # TODO: create a GPerson with it and do a from_gramps on it
                mh = fam.get_mother_handle()
                if mh:
                    if args.verbosity >= 3:
                        print("Mother H:",mh)
                    mother = db.get_person_from_handle(mh)
                    if mother:
                        if args.verbosity >= 1:
                            print("Mother name:",mother.primary_name.get_name())
                        self.mgid = mother.gramps_id
                        # TODO: create a GPerson with it and do a from_gramps on it
        except:
            if args.verbosity >= 1:
                print(_("NOTE: Unable to retrieve family for id %s")%(gid))

    def is_similar(self,p):
        '''
        Return True if 2 GPersons seem identical
        '''

        ret = True
        if self.firstname != p.firstname \
        or self.lastname != p.lastname:
            ret = False
        if self.birthdate and p.birthdate:
            if self.birthdate != p.birthdate:
                ret = False
        if self.deathdate and p.deathdate:
            if self.deathdate != p.deathdate:
                ret = False
        if ret:
            if args.verbosity >= 1:
                print("GPerson %s and %s are similar")%(self.gid,p.gid)
            return(True)
        else:
            if args.verbosity >= 3:
                print("GPerson %s and %s are different")%(self.gid,p.gid)
            return(False)

    def recurse_parents(self,level):
        '''
        analyze the parents of the person passed in parameter recursively
        '''
        # Recurse while we have parents urls and level not reached
        if level <= args.level and (self.fref != "" or self.mref != ""):
            level = level + 1
            time.sleep(TIMEOUT)
            # TODO: what about pre-existing parent
            try:
                fgid = self.father.gid
            except:
                fgid = None
            try:
                mgid = self.mother.gid
            except:
                mgid = None
            gp0 = geneanet_to_gramps(level,fgid,self.fref)
            gp1 = geneanet_to_gramps(level,mgid,self.mref)

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
            f = GFamily()
            f.create(father,mother)

            # Now do what is needed depending on options
            if args.descendants:
                # TODO: First spouse just for now
                father.recurse_children(level,0)
            else:
                # TODO: What about existing children ?
                f.add_child(self)
    
        if level > args.level:
            if args.verbosity >= 1:
                print("Stopping exploration as we reached level "+str(level))
        else:
            if args.verbosity >= 1:
                print("Stopping exploration as there are no more parents")

    def recurse_children(self,level,s):
        '''
        analyze recursively the children of the person passed in parameter 
        with his spouse (number in the list)
        '''
        try:
            cpt = len(self.childref[s])
        except:
            if args.verbosity >= 1:
                print("Stopping exploration as there are no more children")
            return
        # Recurse while we have children urls and level not reached
        if level <= args.level and (cpt > 0):
            level = level + 1
            time.sleep(TIMEOUT)

            # Create or get their spouse and corresponding family
            spouse = self.spouse[s]
            if not spouse:
                # Create it from Geneanet if it exists there
                spouse = geneanet_to_gramps(level,None,self.spouseref[s])
            f = self.family[s]

            if not f:
                print("WARNING: No family found whereas there should be one :-(")
                return

            # What children are in gramps
            # Create a list of GPerson from them
            crl = []
            for c in f.family.get_child_ref_list():
                gp = db.get_person_from_handle(c.ref)
                gid = gp.get_gramps_id()
                p = GPerson(level)
                p.from_gramps(gid)
                crl.append(p)

            # What children are in geneanet
            # Create a list of GPerson from them
            # Compare and merge
            for c in self.childref[s]:
                # TODO: Assume there is no such child already existing
                gpc = GPerson(level)
                gpc.from_geneanet(c)

                # Compare this person to the one in Gramps and if one matches, update it
                # If not add it
                found = None
                for p in crl:
                    if gpc.is_similar(p):
                        found = p
                if not found:
                    found = GPerson(level)
                found.to_gramps()
                f.add_child(found)

                if args.verbosity >= 2:
                    print("=> Recursing on the child of "+self.firstname+" "+self.lastname+' : '+gpc.firstname+' '+gpc.lastname)
                # Recurse with first spouse only for now
                gpc.recurse_children(level,0)
                if args.verbosity >= 2:
                    print("=> End of recursing on the child of "+self.firstname+" "+self.lastname+' : '+gpc.firstname+' '+gpc.lastname)

        if level > args.level:
            if args.verbosity >= 1:
                print("Stopping exploration as we reached level "+str(level))

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
    # Create the Person coming from Geneanet
    p = GPerson(level)
    p.from_geneanet(url)

    # Create the Person coming from Gramps
    # Done after so we can try to find it in Gramps with the Geneanet data
    p.from_gramps(gid)

    # Check we point to the same person
    if gid != None:
        if (p.firstname != p.g_firstname or p.lastname != p.g_lastname) and (not args.force):
            print("Gramps   person: %s %s"%(p.firstname,p.lastname))
            print("Geneanet person: %s %s"%(p.g_firstname,p.g_lastname))
            db.close()
            sys.exit("Do not continue without force")

        if (p.birthdate != p.g_birthdate or p.deathdate != p.g_deathdate) and (not args.force):
            print("Gramps   person birth/death: %s / %s"%(p.birthdate,p.deathdate))
            print("Geneanet person birth/death: %s / %s"%(p.g_birthdate,p.g_deathdate))
            db.close()
            sys.exit("Do not continue without force")

    # Copy from Geneanet into Gramps and commit
    p.to_gramps()
    return(p)

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
    parser.add_argument("-s", "--spouse", default=False, action='store_true', help="Includes all spouses (off by default)")
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
	
    # TODO: do a backup before opening
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
    
    # TODO: We need to handle spouses first and create them if needed

    LEVEL = 0
    if args.descendants:
        # TODO: First spouse just for now
        gp.recurse_children(LEVEL,0)
    
    db.close()
    sys.exit(0)
    

if __name__ == '__main__':
    main()

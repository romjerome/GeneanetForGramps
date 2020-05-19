#!/usr/bin/python3

import sys
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import open_database
from gramps.gen.dbstate import DbState
from gramps.cli.grampscli import CLIManager
from gramps.gen.lib import Person, Name, Surname, NameType, Event, EventType, Date, Place, EventRoleType, EventRef, PlaceName

name = "/users/bruno/.gramps/grampsdb/5ec17554"

dbstate = DbState()
climanager = CLIManager(dbstate, True, None)
climanager.open_activate(name)
db = dbstate.db
gid = "I0001"
with DbTxn("Import", db) as tran:
    #db.disable_signals()
    grampsp = Person()
    grampsp.set_gender(Person.MALE)
    n = Name()
    n.set_type(NameType(NameType.BIRTH))
    n.set_first_name("Jean")
    s = n.get_primary_surname()
    s.set_surname("Bon")
    grampsp.set_primary_name(n)

    event = Event()
    event.set_description('Imported')
    event.set_type(EventType(EventType.BIRTH))
    db.add_event(event,tran)
    eventref = EventRef()
    eventref.set_role(EventRoleType.PRIMARY)
    eventref.set_reference_handle(event.get_handle())
    grampsp.set_birth_ref(eventref)

    date = Date()
    date.set_yr_mon_day(2020,5,18)
    event.set_date_object(date)
    db.commit_event(event,tran)

    place = Place()
    place.set_name(PlaceName(value="LaPlace"))
    place.set_title("laplace")
    place.set_code("99999")
    db.add_place(place,tran)
    event.set_place_handle(place.get_handle())
    db.commit_event(event,tran)

    event = Event()
    event.set_description('Imported')
    event.set_type(EventType(EventType.DEATH))
    db.add_event(event,tran)
    eventref = EventRef()
    eventref.set_role(EventRoleType.PRIMARY)
    eventref.set_reference_handle(event.get_handle())
    grampsp.set_death_ref(eventref)
    db.add_person(grampsp,tran)
    #db.enable_signals()
#db.request_rebuild()
db.close()
sys.exit()

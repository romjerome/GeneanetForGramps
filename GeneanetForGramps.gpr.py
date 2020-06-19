#
# Copyright (C) 2020 Bruno Cornec
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# $Id: $

#------------------------------------------------------------------------
#------------------------------------------------------------------------

register(TOOL,
    id    = 'Import Geneanet data for Gramps',
    name  = _("Import Geneanet data for Gramps"),
    name_accell  = _("Geneanet for Gramps"),
    description =  _("Extension to import data from Geneanet into Gramps."),
    version = '1.0.0',
    gramps_target_version = '5.1',
    status = STABLE, 
    fname = 'GeneanetForGramps.py',
    authors = ['Bruno Cornec'],
    authors_email = ['bruno@flossita.org'],
    category = TOOL_UTILS,
    toolclass = 'GeneanetForGramps',
    optionclass = 'GeneanetForGrampsOptions',
    tool_modes = [TOOL_MODE_GUI],
)

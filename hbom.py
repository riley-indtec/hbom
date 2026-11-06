# hbom.py
# Author: Riley Mann
#
# try `hbom -h`
#
# Created on 1 Nov 2026

import argparse
from datetime import datetime
import csv
import json
import os
import re
import sqlite3

_settings_path = os.path.expanduser('~/.hbom/settings.json')

_default_settings = {
    'dbpath': os.path.expanduser('~/.hbom/hbom_database.db'),
    'headerpatterns': {
        'description': '(component )?desc\\.?(ription)?',
        'product identifier': '(mfg\\.?|manufacturer) p(art)?[ /.]?n(um)?\\.?(ber)?',
        'reference designator': 'ref\\.?(erence)?( designator)?',
        'quantity': 'qty\\.?|quantity'
    },
    'highlightcolors': {
        'pthtop': 'rgb("#ffff00")',
        'pthbot': 'aqua',
        'smdtop': 'lime',
        'smdbot': 'rgb("#c958da")',
    },
    'pkgpatterns': {
        'pth': ['PTH'],
        'smd': ['SMD']
    }
}

typ_indent = 2  # typst document indent


# Handle special characters for use in typst content
def typstify(msg):
    codes = ['*', '_', '`', '<', '>', '@', '=', '-', '+', '/', '$', "'", '"', '~', '#']
    msg = msg.replace('\\', '\\\\')
    for code in codes:
        msg = msg.replace(code, '\\' + code)
    return msg


def typ_template(path, datestr, columns, pre_header, refd_col, highlight_colors):
    highlight_styling = ''
    for tag, color in highlight_colors.items():
        highlight_styling += f'#show <{tag}>: it => highlight(fill: {color}, it)\n\n'
    return '''// {}
// Highlighted BOM generated using hbom
// Created on {}

/* Document styling */

#set page(
  paper: "us-letter",
  flipped: true,
  margin: (top: .2in + 2em, rest: .2in),
  header: [{} #h(1fr) #datetime.today().display("[month]/[day]/[year]")],
  header-ascent: 1em,
)

#set text(
  font: "DejaVu Sans Mono",
  size: 8pt,
)

/* Table styling */

#set table(
  columns: {},
  fill: (_, y) => if y == {} {{ gray }},
  align: (x, y) => if x == {} and y != {} {{left}} else {{center}},
)

#set table.header(repeat: true)

#set table.cell(breakable: false)

#show table.cell.where(y: {}): strong

/* Reference designator highlighting */

{}/* Content */

#table(
  table.header(
'''.format(
        path, typstify(datestr), typstify(path), columns, pre_header, refd_col,
        pre_header, pre_header, highlight_styling)


# App object
class App:
    def __init__(self, bom_path, pick_path, output_path, pre_header, skip_prompts, verbose):
        self._bom_path = bom_path
        self._pick_path = pick_path
        self._output_path = output_path
        self._pre_header = pre_header
        self._skip_prompts = skip_prompts
        self._verbose = verbose

        self._settings = _default_settings.copy()

    def settings_init(self, settings_path):
        # Create a new settings file if it doesn't exist
        if not os.path.isfile(settings_path):
            if self._verbose:
                print(f"creating new settings file '{settings_path}'\n")
            dir = os.path.dirname(settings_path)
            if dir:
                os.makedirs(dir, exist_ok=True)
            with open(settings_path, 'w') as f:
                json.dump(self._settings, f, indent=4 * ' ')

        # Otherwise, retrieve existing settings
        else:
            if self._verbose:
                print(f"loading settings from '{settings_path}'\n")
            with open(settings_path, 'r') as f:
                local_settings = json.load(f)

                def dict_update(old, new):
                    for key, value in new.items():
                        if key not in old:
                            raise Exception(f'invalid setting "{key}" in \'{_settings_path}\'')
                        if type(new[key]) is not type(old[key]):
                            raise Exception('in \'{}\', "{}" should be type {}'.format(
                                _settings_path, key, type(old[key])))
                        if type(old[key]) is dict:
                            dict_update(old[key], new[key])
                        else:
                            old[key] = new[key]
                dict_update(self._settings, local_settings)

    def database_init(self):
        # Connect to database
        if self._verbose:
            print(f"connecting to database '{self._settings['dbpath']}'")
        con = sqlite3.connect(self._settings['dbpath'])
        cur = con.cursor()

        # Create pkgmem table
        try:
            cur.execute('''
                CREATE TABLE pkgmem (
                    id TEXT NOT NULL UNIQUE,
                    desc TEXT,
                    pkgtype TEXT
                )''')
            if self._verbose:
                print('created new table in database')
        except sqlite3.OperationalError:
            pass

        con.close()
        if self._verbose:
            print()

    def generate_hbom(self):
        # Check if files exist
        files_exist = True
        if not os.path.isfile(self._bom_path):
            print(f"BOM file '{self._bom_path}' not found")
            files_exist = False
        if self._pick_path:
            if not os.path.isfile(self._pick_path):
                print(f"pick and place file '{self._pick_path}' not found")
                files_exist = False
        if not files_exist:
            print('exit')
            return None

        # Read BOM file
        pretable = []
        header = None
        table = []
        if self._verbose:
            print(f"reading '{self._bom_path}...'")
        with open(self._bom_path, 'r') as bom_file:
            bom_reader = csv.reader(bom_file)
            for i, row in enumerate(bom_reader):
                # Pretable
                if i < self._pre_header:
                    pretable.append(row)

                # Header
                elif i == self._pre_header:
                    header = row

                # Rest of the table
                else:
                    table.append(row)
        if self._verbose:
            print(f"finished reading '{self._bom_path}'")

        # Print header
        if self._verbose:
            print('header:', ', '.join([f'[{name}]' for name in header]))
            print('selecting columns...')

        # Automatically locate columns using header names
        col = {key: None for key in self._settings['headerpatterns']}
        for j, name in enumerate(header):
            patterns = self._settings['headerpatterns']
            for key in patterns:
                if re.fullmatch(patterns[key], name, re.IGNORECASE):
                    if col[key] is None:
                        col[key] = j
                        if self._verbose:
                            print(f"found the {key} column: [{name}]")

        # Manually select any required columns not already found
        for key, value in col.items():
            if value is None:
                print(f"couldn't find the {key} column")
                for j, name in enumerate(header):
                    print(f'{j}: [{name}]')
                while True:
                    ans = input(f"Select the {key} column [0-{len(header) - 1}] ('q' to quit): ")
                    if ans.lower() == 'q':
                        print('exit')
                        return None
                    try:
                        ans_int = int(ans)
                        if ans_int in range(len(header)):
                            col[key] = ans_int
                            print(f"selected [{header[ans_int]}]")
                            break
                    except ValueError:
                        pass
                    print(f"invalid column '{ans}'")

        # Print selected columns
        if self._verbose:
            print('finished selecting columns')

        # Connect to database
        con = sqlite3.connect(self._settings['dbpath'])
        cur = con.cursor()

        # Parse table
        if self._verbose:
            print('parsing table...')
        for i, row in enumerate(table):
            if self._verbose:
                print(f"\nROW {i}:", ', '.join([f'[{value}]' for value in row]))

            desc = row[col['description']]
            pid = row[col['product identifier']]
            refds = row[col['reference designator']]
            qty = row[col['quantity']]

            # Convert quantities to integers
            try:
                row[col['quantity']] = int(qty)
            except ValueError:
                pass

            # Highlight reference designators
            if refds:
                if self._verbose:
                    print('row contains loaded parts')

                # Find package type: pth or smd
                pkgtype = None
                if self._verbose:
                    print(f'determining package type for row {i}...')

                # Try searching the description
                if desc:
                    for key, patterns in self._settings['pkgpatterns'].items():
                        for pattern in patterns:
                            if re.search('\\b' + pattern + '\\b', desc, re.IGNORECASE):
                                pkgtype = key
                                if self._verbose:
                                    print(f"'{desc}' -> {pkgtype}")

                # Try the database
                if pkgtype is None and pid:
                    result = cur.execute('''
                        SELECT pkgtype
                        FROM pkgmem
                        WHERE id = '{}'
                        '''.format(pid)).fetchone()
                    if result:
                        pkgtype = result[0]
                        if self._verbose:
                            print(f"found '{pid}' in database -> {pkgtype}")

                # Lastly, prompt the user
                if pkgtype is None:
                    print('unable to determine package type for the following:')
                    print()
                    for key, value in zip(header, row):
                        print(f'[{key}]: {value}')
                    print()
                    if not self._skip_prompts:
                        while True:
                            ans = input(f"Give a package type for row {i} '{desc}' {[key for key in self._settings['pkgpatterns']] + ['none']} ('q' to quit): ")
                            if ans.lower() == 'q':
                                print('exit')
                                return None
                            if ans.lower() == 'none':
                                break
                            if ans.lower() in self._settings['pkgpatterns']:
                                pkgtype = ans.lower()
                                print(f'selected {pkgtype}')
                                break
                            print(f"invalid package type '{ans}'")

                        # Save decision to database
                        if pkgtype is not None and pid:
                            while True:
                                ans = input(f"Save '{pid}' as {pkgtype} to database? [y/n] ('q' to quit): ")
                                if ans.lower() == 'q':
                                    print('exit')
                                    return None
                                if ans.lower() == 'y':
                                    cur.execute('''
                                        INSERT INTO pkgmem
                                        VALUES ('{}', '{}', '{}')
                                        '''.format(pid, desc, pkgtype))
                                    con.commit()
                                    print(f"saved '{pid}' to database")
                                    break
                                if ans.lower() == 'n':
                                    break
                                print(f"invalid answer '{ans}'")

                if self._verbose:
                    print(f'finished determining package type for row {i} -> {pkgtype}')

                # Build tagged reference designators list
                tagged_list = []
                row_face = None
                ask_to_repeat = True
                if self._verbose:
                    print(f'tagging reference designators for row {i}...')
                for refd in [item.strip() for item in refds.split(',')]:
                    # Determine board face
                    face = None

                    if pkgtype:
                        # If the row is all on the same face repeat the face assignment
                        if row_face:
                            face = row_face
                            if self._verbose:
                                print(f"repeating '{row_face}' for '{refd}'")

                        # Otherwise, determine the board face
                        else:
                            if self._verbose:
                                print(f"determining board face for '{refd}'...")

                            # Try the pick-and-place file
                            if self._pick_path:
                                with open(self._pick_path, 'r') as f:
                                    for line in f:
                                        if re.search('\\b' + re.escape(refd) + '\\b', line):
                                            if re.search('\\btop', line, re.IGNORECASE):
                                                face = 'top'
                                                break
                                            if re.search('\\bbot', line, re.IGNORECASE):
                                                face = 'bot'
                                                break
                                if face and self._verbose:
                                    print(f"found face for '{refd}' in '{self._pick_path}' -> {face}")

                            # lastly, prompt the user
                            if face is None:
                                print(f"unable to determine face for '{refd}'.")
                                if not self._skip_prompts:
                                    options = ['top', 'bot']
                                    while True:
                                        ans = input(f"Enter face for '{refd}' {options + ['none']} ('q' to quit): ")
                                        if ans.lower() == 'q':
                                            print('exit')
                                            return None
                                        if ans.lower() == 'none':
                                            break
                                        if ans.lower() in options:
                                            face = ans.lower()
                                            print(f"selected '{face}'")
                                            break
                                        print(f"invalid face '{ans}'")

                                    # Prompt user if the row is all the same
                                    if ask_to_repeat and face:
                                        while True:
                                            ans = input(f"Are [{refds}] all on the same side? [y/n] ('q' to quit): ")
                                            if ans.lower() == 'q':
                                                print('exit')
                                                return None
                                            if ans.lower() == 'y':
                                                row_face = face
                                                break
                                            if ans.lower() == 'n':
                                                break
                                            print(f"invalid answer '{ans}'")
                                        ask_to_repeat = False

                        if self._verbose:
                            print(f"finished determining board face type for '{refd}' -> {face}")

                    if pkgtype and face:
                        tagged_list.append(f'#box[{refd}]<{pkgtype}{face}>')
                        if self._verbose:
                            print(f"tagged '{refd}' with <{pkgtype}{face}>")
                    else:
                        tagged_list.append(f'#box[{refd}]')

                # replace the reference designators string
                row[col['reference designator']] = ','.join(tagged_list)
                if self._verbose:
                    print(f'finished tagging reference designators for row {i}')
        if self._verbose:
            print('finished parsing table')

        # Close database
        con.close()

        # Generate typst document
        output_path = self._output_path
        if output_path is None:
            output_path = os.path.splitext(
                os.path.basename(self._bom_path))[0] + '_highlighted.typ'
        if self._verbose:
            print(f"writing to '{output_path}'...")
        dir = os.path.dirname(output_path)
        if dir:
            os.makedirs(dir, exist_ok=True)
        with open(output_path, 'w') as f:
            # Template
            f.write(typ_template(output_path, datetime.now().strftime("%d %b %Y"), len(header),
                    self._pre_header, col['reference designator'],
                    self._settings['highlightcolors']))

            # Fill table
            for row in pretable:
                f.write((2 * typ_indent - 1) * ' ')
                for item in row:
                    f.write(f' [{typstify(item)}],')
                f.write('\n')
            f.write((2 * typ_indent - 1) * ' ')
            for item in header:
                f.write(f' [{typstify(item)}],')
            f.write('\n' + typ_indent * ' ' + '),\n')
            for row in table:
                f.write((typ_indent - 1) * ' ')
                for j, item in enumerate(row):
                    if j != col['reference designator'] and type(item) is str:
                        item = typstify(item)
                    f.write(f' [{item}],')
                f.write('\n')
            f.write(')\n')

        print(f"saved to '{output_path}'")
        print('done')
        return output_path


# Parse CLI arguments
parser = argparse.ArgumentParser(
    description='A PCA BOM table highlighter tool')
parser.add_argument('BOM', help='BOM file to be highlighted')
parser.add_argument(
    '-x', '--pick',
    nargs='?',
    help='pick and place file')
parser.add_argument(
    '-o', '--output',
    nargs='?',
    help='output file to write to')
parser.add_argument(
    '-p', '--pre-header',
    nargs='?', default=0, type=int,
    help='rows before the header (default: 0)')
parser.add_argument(
    '-y', '--yes',
    action='store_true',
    help='skip unrequired user prompts')
parser.add_argument(
    '-v', '--verbose',
    action='store_true',
    help='show all status messages')
args = parser.parse_args()

app = App(args.BOM, args.pick, args.output, args.pre_header, args.yes, args.verbose)
app.settings_init(_settings_path)
app.database_init()
app.generate_hbom()

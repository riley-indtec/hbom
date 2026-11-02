# hbom.py
# Author: Riley Mann
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
    'dbpath': os.path.expanduser('~/.hbom/saves.db'),
    'headerpatterns': {
        'description': 'desc\\.?(ription)?',
        'product identifier': '(mfg\\.?|manufacturer) p(art)?[ /.]?n(um)?\\.?(ber)?',
        'reference designator': 'ref\\.?(erence)?( designator)?',
        'quantity': 'qty\\.?|quantity'
    },
    'pkgpatterns': {
        'pth': ['PTH'],
        'smd': ['SMD']
    }
}

# Typst document building blocks

# Typst building blocks
typ_comment_block = '''// {}
// Highlighted BOM generated using hbom
// Created on {}
'''

typ_styling = '''
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

#show <pthtop>: it => highlight(fill: rgb("#ffff00"), it)

#show <pthbot>: it => highlight(fill: aqua, it)

#show <smdtop>: it => highlight(fill: lime, it)

#show <smdbot>: it => highlight(fill: rgb("#dd97e8"), it)
'''

typ_table = '''
/* Content */

#table(
    table.header(
'''


# App object
class App:
    def __init__(self, bompath, locpath, outpath, header, skip_prompts):
        if not os.path.isfile(bompath):
            raise FileNotFoundError(f"BOM file '{bompath}' not found")
        self._bompath = bompath
        if locpath is not None:
            if not os.path.isfile(locpath):
                raise FileNotFoundError(f"component locations file '{locpath}' not found")
        self._locpath = locpath
        self._outpath = outpath
        self._header = header
        self._skip_prompts = skip_prompts

        # Get settings
        self._settings = _default_settings.copy()

    def settings_init(self, settings_path):
        # Create a new settings file if it doesn't exist
        if not os.path.isfile(settings_path):
            dir = os.path.dirname(settings_path)
            if dir:
                os.makedirs(dir, exist_ok=True)
            with open(settings_path, 'w') as f:
                json.dump(self._settings, f, indent=4 * ' ')

        # Otherwise, retrieve existing settings
        else:
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
        con = sqlite3.connect(self._settings['dbpath'])
        cur = con.cursor()

        # Create pkgmem table
        try:
            cur.execute('''
                CREATE TABLE pkgmem (
                    id TEXT NOT NULL UNIQUE,
                    pkgtype TEXT
                )''')
        except sqlite3.OperationalError:
            pass

        con.close()

    def run(self):
        pretable = []
        header = None
        table = []
        col = {key: None for key in self._settings['headerpatterns']}
        with open(self._bompath, 'r') as bom_file:
            bom_reader = csv.reader(bom_file)
            for bom_i, row in enumerate(bom_reader):
                # Pretable
                if bom_i < (self._header - 1):
                    pretable.append(row)

                # Header
                elif bom_i == (self._header - 1):
                    header = row

                # Rest of the table
                else:
                    table.append(row)

        # Parse header
        print('parsing header...')

        # Automatically locate columns using header names
        for j, name in enumerate(header):
            patterns = self._settings['headerpatterns']
            for key in patterns:
                if re.fullmatch(patterns[key], name, re.IGNORECASE):
                    if col[key] is None:
                        col[key] = j

        # Manually select any required columns not already found
        for key, value in col.items():
            if value is None:
                print(f'\nUnable to find the {key} column.')
                for j, name in enumerate(header):
                    print(f'[{j}] {name}')
                while True:
                    ans = input(f"Pick a column [0-{len(header) - 1}] ('q' to exit): ")
                    if ans.lower() == 'q':
                        print('exit')
                        return
                    try:
                        ans_int = int(ans)
                        if ans_int in range(len(header)):
                            col[key] = ans_int
                            break
                    except ValueError:
                        pass
                    print(f"invalid column '{ans}'")

        # Print selected columns
        for key, value in col.items():
            print(f"{key} column <- [{header[value]}]")

        # Connect to database
        con = sqlite3.connect(self._settings['dbpath'])
        cur = con.cursor()

        # Parse table
        print('parsing table...')
        for row in table:
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
                # Find package type: pth or smd
                pkgtype = None
                if desc:  # try searching the description
                    for key, patterns in self._settings['pkgpatterns'].items():
                        for pattern in patterns:
                            if re.search('\\b' + pattern + '\\b', desc, re.IGNORECASE):
                                pkgtype = key
                if pkgtype is None and pid:  # try the database
                    result = cur.execute('''
                        SELECT pkgtype
                        FROM pkgmem
                        WHERE id = '{}'
                        '''.format(pid)).fetchone()
                    if result:
                        pkgtype = result[0]
                if pkgtype is None:  # lastly, prompt the user
                    print('\nUnable to determine package type for the following product:')
                    for key, value in zip(header, row):
                        print(f'[{key}]: {value}')
                    if not self._skip_prompts:
                        while True:
                            ans = input('Pick a package type {}: '.format(
                                [key for key in self._settings['pkgpatterns']] + ['none']))
                            if ans.lower() == 'none':
                                break
                            if ans.lower() in self._settings['pkgpatterns']:
                                pkgtype = ans.lower()
                                break
                            print(f"invalid package type '{ans}'")

                        # Save decision
                        if pkgtype is not None and pid:
                            while True:
                                ans = input(f"Save '{pid}' as '{pkgtype}' to database? (y/n): ")
                                if ans.lower() == 'y':
                                    cur.execute('''
                                        INSERT INTO pkgmem
                                        VALUES ('{}', '{}')
                                        '''.format(pid, pkgtype))
                                    con.commit()
                                    break
                                if ans.lower() == 'n':
                                    break
                                print(f"invalid answer '{ans}'")

                # Build tagged reference designators list
                tagged_list = []
                for refd in [item.strip() for item in refds.split(',')]:
                    # Determine board face
                    face = None
                    if self._locpath:  # try the pick-and-place file
                        with open(self._locpath, 'r') as f:
                            for line in f:
                                if re.search('\\b' + refd + '\\b', line):
                                    if re.search('\\btop', line, re.IGNORECASE):
                                        face = 'top'
                                        break
                                    if re.search('\\bbot', line, re.IGNORECASE):
                                        face = 'bot'
                                        break
                    if face is None:  # lastly, prompt the user
                        print(f"\nUnable to determine face for '{refd}'.")
                        if not self._skip_prompts:
                            while True:
                                ans = input(f"Enter face for '{refd}' ['top', 'bot', 'none']: ")
                                if ans.lower() == 'none':
                                    break
                                if ans.lower() == 'top':
                                    face = 'top'
                                    break
                                if ans.lower() == 'bot':
                                    face = 'bot'
                                    break
                                print(f"invalid face '{ans}'")

                    # If undetermined, append an untagged reference designator
                    if pkgtype is None or face is None:
                        tagged_list.append(f'#box[{refd}]')
                        continue

                    # Otherwise append a tagged reference designator
                    tagged_list.append(f'#box[{refd}]<{pkgtype}{face}>')

                # replace the reference designators string
                row[col['reference designator']] = ','.join(tagged_list)

        # Close database
        con.close()

        # Generate typst document
        output_path = self._outpath
        if output_path is None:
            output_path = os.path.splitext(os.path.basename(self._bompath))[0] + '.typ'
        print(f"writing to '{output_path}'...")
        dir = os.path.dirname(output_path)
        if dir:
            os.makedirs(dir, exist_ok=True)
        with open(output_path, 'w') as f:
            # Comment Block
            f.write(typ_comment_block.format(
                os.path.basename(output_path),
                datetime.now().strftime("%d %b %Y")))

            # Styling
            f.write(typ_styling.format(
                output_path,
                len(header),
                self._header - 1,
                col['reference designator'],
                self._header - 1,
                self._header - 1))

            # Table
            f.write(typ_table.format(os.path.splitext(os.path.basename(output_path))[0]))
            for row in pretable:
                f.write(7 * ' ')
                for item in row:
                    item = item.replace('#', '\\#').replace('@', '\\@')
                    f.write(f' [{item}],')
                f.write('\n')
            f.write(7 * ' ')
            for item in header:
                item = item.replace('#', '\\#').replace('@', '\\@')
                f.write(f' [{item}],')
            f.write('\n    ),\n')
            for row in table:
                f.write(3 * ' ')
                for j, item in enumerate(row):
                    if j != col['reference designator'] and type(item) is str:
                        item = item.replace('#', '\\#').replace('@', '\\@')
                    f.write(f' [{item}],')
                f.write('\n')
            f.write(')\n')

        print('done')


# Parse CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument('BOM', help='BOM file to be highlighted')
parser.add_argument(
    '--loc',
    nargs='?',
    help='text file with component locations')
parser.add_argument(
    '-o', '--output',
    nargs='?',
    help='output file to write to')
parser.add_argument(
    '--header',
    nargs='?', default=1, type=int,
    help='BOM row that contains the header (default: 1)')
parser.add_argument(
    '-y', '--yes',
    action='store_true',
    help='skip unrequired user prompts')
args = parser.parse_args()

app = App(args.BOM, args.loc, args.output, args.header, args.yes)
app.settings_init(_settings_path)
app.database_init()
app.run()

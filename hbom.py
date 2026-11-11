# hbom.py
# Author: Riley Mann
#
# try `hbom -h`
#
# Created on 1 Nov 2026

import argparse
from datetime import datetime
from colorama import Fore
import csv
import json
import os
import re
import sqlite3
import sys

_settings_path = os.path.expanduser('~/.hbom/settings.json')

_default_settings = {
    'dbpath': os.path.expanduser('~/.hbom/saves.db'),
    'headerpatterns': {
        'description': '(component )?desc\\.?(ription)?',
        'product identifier': '(mfg\\.?|manufacturer) p(art)?[ /.]?n(um)?\\.?(ber)?',
        'reference designator': 'ref\\.?(erence)?( designators?)?',
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
    def __init__(self, bom_path, pick_path, output_path, pre_header, skip_prompts):
        self._bom_path = bom_path
        self._pick_path = pick_path
        self._output_path = output_path
        self._pre_header = pre_header
        self._skip_prompts = skip_prompts

        self._settings = _default_settings.copy()

    def settings_init(self, settings_path):
        # Create a new settings file if it doesn't exist
        if not os.path.isfile(settings_path):
            print(f"creating new settings file '{Fore.CYAN}{settings_path}{Fore.RESET}'")
            dir = os.path.dirname(settings_path)
            if dir:
                os.makedirs(dir, exist_ok=True)
            with open(settings_path, 'w') as f:
                json.dump(self._settings, f, indent=4 * ' ')

        # Otherwise, retrieve existing settings
        else:
            print(f"loading settings from '{Fore.CYAN}{settings_path}{Fore.RESET}'")
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
        print(f"connecting to database '{Fore.CYAN}{self._settings['dbpath']}{Fore.RESET}'")
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
            print(f"created new table '{Fore.YELLOW}pkgmem{Fore.RESET}' in database")
        except sqlite3.OperationalError:
            pass

        con.close()

    def close(self):
        print('exit')
        sys.exit()

    def _bool_prompt(self, prompt):
        while True:
            ans = input(f"{prompt} {Fore.GREEN}[y/n]{Fore.RESET} ('q' to quit): ")
            if ans.lower() in ['q', 'quit']:
                self.close()
            if ans.lower() in ['y', 'yes']:
                return True
            if ans.lower() in ['n', 'no']:
                return False
            print(f"{Fore.RED}invalid answer{Fore.RESET} '{ans}'")

    def _num_prompt(self, prompt, n):
        while True:
            ans = input(f"{prompt} {Fore.GREEN}[0-{n}]{Fore.RESET} ('q' to quit): ")
            if ans.lower() in ['q', 'quit']:
                self.close()
            try:
                ans_int = int(ans)
                if ans_int in range(n):
                    return ans_int
            except ValueError:
                pass
            print(f"{Fore.RED}invalid option{Fore.RESET} '{ans}'")

    def _options_prompt(self, prompt, options):
        options = [option.lower() for option in options]
        while True:
            ans = input(f"{prompt} {Fore.GREEN}{options + ['none']}{Fore.RESET} ('q' to quit): ")
            if ans.lower() in ['q', 'quit']:
                self.close()
            if ans.lower() == 'none':
                return None
            if ans.lower() in options:
                return ans.lower()
            print(f"{Fore.RED}invalid option{Fore.RESET} '{ans}'")

    def _read_bom(self):
        pretable = []
        header = None
        table = []
        print(f"reading '{Fore.CYAN}{self._bom_path}{Fore.RESET}'...")
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

        print(f"finished reading '{Fore.CYAN}{self._bom_path}{Fore.RESET}'")
        return pretable, header, table

    def _index_cols(self, header):
        print('selecting columns...')

        # Automatically locate columns using header names
        col = {key: None for key in self._settings['headerpatterns']}
        for j, name in enumerate(header):
            patterns = self._settings['headerpatterns']
            for key in patterns:
                if re.fullmatch(patterns[key], name, re.IGNORECASE):
                    if col[key] is None:
                        col[key] = j
                        print(f"found the {key} column: [{Fore.YELLOW}{name}{Fore.RESET}]")

        # Manually select any required columns not already found
        for key, value in col.items():
            if value is None:
                print(f"couldn't find the {key} column")
                for j, name in enumerate(header):
                    print(f'{Fore.GREEN}{j}{Fore.RESET}: [{Fore.YELLOW}{name}{Fore.RESET}]')
                col[key] = self._num_prompt(f'Select the {key} column', len(header))
                print(f'selected [{Fore.YELLOW}{header[col[key]]}{Fore.RESET}]')

        print('finished selecting columns')
        return col

    def _determine_pkgtype(self, header, row, col, con, cur):
        print('determining package type...')
        pid = row[col['product identifier']]
        desc = row[col['description']]

        # First, try searching the description
        if desc:
            for pkgtype, patterns in self._settings['pkgpatterns'].items():
                for pattern in patterns:
                    if re.search('\\b' + pattern + '\\b', desc, re.IGNORECASE):
                        print("description '{}' matches {} package type".format(
                            Fore.YELLOW + desc + Fore.RESET,
                            Fore.YELLOW + pkgtype + Fore.RESET))
                        return pkgtype

        # Next, try the database
        if pid:
            result = cur.execute('''
                SELECT pkgtype
                FROM pkgmem
                WHERE id = '{}'
                '''.format(pid)).fetchone()
            if result:
                pkgtype = result[0]
                print("found '{}' in database as {} package type".format(
                    Fore.YELLOW + pid + Fore.RESET,
                    Fore.YELLOW + pkgtype + Fore.RESET))
                return pkgtype

        # Lastly, prompt the user
        pkgtype = None
        print('unable to determine package type for the following:')
        for key, value in zip(header, row):
            print(f'[{key}]: {Fore.YELLOW}{value}{Fore.RESET}')
        if not self._skip_prompts:
            pkgtype = self._options_prompt(
                "Give a package type for PID: '{}', DESC: '{}'".format(
                    Fore.YELLOW + pid + Fore.RESET,
                    Fore.YELLOW + desc + Fore.RESET),
                [key for key in self._settings['pkgpatterns']])
            print(f'selected {Fore.YELLOW}{pkgtype}{Fore.RESET}')

            # Save decision to database
            if pkgtype is not None and pid:
                prompt = "Save '{}' as {} to database?".format(
                    Fore.YELLOW + pid + Fore.RESET,
                    Fore.YELLOW + pkgtype + Fore.RESET)
                if self._bool_prompt(prompt):
                    cur.execute('''
                        INSERT INTO pkgmem
                        VALUES ('{}', '{}', '{}')
                        '''.format(pid, desc, pkgtype))
                    con.commit()
                    print("saved '{}' | '{}' | '{}' to database".format(
                        Fore.YELLOW + pid + Fore.RESET,
                        Fore.YELLOW + desc + Fore.RESET,
                        Fore.YELLOW + pkgtype + Fore.RESET))
        return pkgtype

    def _tag(self, refds_list, pkgtype):
        print('tagging reference designators...')
        tagged = []
        repeat = None

        for i, refd in enumerate(refds_list):
            face = None
            if pkgtype:
                print(f"determining board face for '{Fore.YELLOW}{refd}{Fore.RESET}'...")

                if repeat:
                    face = repeat

                # First, try the pick-and-place file
                if face is None and self._pick_path:
                    with open(self._pick_path, 'r') as f:
                        look_for_mirror = False
                        for line in f:
                            if re.search('\\bmirror\\b', line):
                                look_for_mirror = True
                            if re.search('(\\A|\\s)' + re.escape(refd) + '\\s', line):
                                if look_for_mirror:
                                    if re.search('\\bm\\b', line):
                                        face = 'bot'
                                    else:
                                        face = 'top'
                                    break
                                if re.search('\\btop', line, re.IGNORECASE):
                                    face = 'top'
                                    break
                                if re.search('\\bbot', line, re.IGNORECASE):
                                    face = 'bot'
                                    break
                    if face:
                        print("found '{}' in '{}' on {} face".format(
                            Fore.YELLOW + refd + Fore.RESET,
                            Fore.CYAN + self._pick_path + Fore.RESET,
                            Fore.YELLOW + face + Fore.RESET))

                # Lastly, prompt the user
                if face is None:
                    print(f"unable to determine board face for '{Fore.YELLOW}{refd}{Fore.RESET}'.")
                    if not self._skip_prompts:
                        options = ['top', 'bot']
                        face = self._options_prompt("Enter face for '{}'".format(
                            Fore.YELLOW + refd + Fore.RESET), options)
                        print(f'selected {Fore.YELLOW}{face}{Fore.RESET}')

                        # Ask to repeat the selection
                        if face and i == 0 and len(refds_list) > 1:
                            prompt = 'Are {} all on the same side?'.format(
                                ','.join([Fore.YELLOW + refd + Fore.RESET for refd in refds_list]))
                            if self._bool_prompt(prompt):
                                repeat = face
                                print("repeating '{}' for all ref. designators in the row".format(
                                    Fore.YELLOW + face + Fore.RESET))

            # Tag and append
            if pkgtype and face:
                tagged.append(f'#box[{refd}]<{pkgtype}{face}>')
                print("tagged '{}' with {}".format(
                    Fore.YELLOW + refd + Fore.RESET,
                    Fore.YELLOW + f'<{pkgtype}{face}>' + Fore.RESET))
            else:
                tagged.append(f'#box[{refd}]')
                print(f"'{Fore.YELLOW}{refd}{Fore.RESET}' is untagged")

        print('finished tagging reference designators')
        return tagged

    def _generate_typst_bom(self, pretable, header, table, col):
        output_path = self._output_path
        if output_path is None:
            output_path = os.path.splitext(
                os.path.basename(self._bom_path))[0] + '_highlighted.typ'

        print(f"writing to '{Fore.CYAN}{output_path}{Fore.RESET}'...")
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

        print(f"finished writing to '{Fore.CYAN}{output_path}{Fore.RESET}'")
        return output_path

    def run(self):
        # Check if files exist
        files_exist = True
        if not os.path.isfile(self._bom_path):
            print(f"{Fore.RED}BOM file '{self._bom_path}' not found{Fore.RESET}")
            files_exist = False
        if self._pick_path:
            if not os.path.isfile(self._pick_path):
                print(f"{Fore.RED}pick and place file '{self._pick_path}' not found{Fore.RESET}")
                files_exist = False
        if not files_exist:
            self.close()

        # Read BOM file
        pretable, header, table = self._read_bom()

        print('header:', ', '.join([f'[{Fore.YELLOW}{name}{Fore.RESET}]' for name in header]))

        # Find inportant column indices
        col = self._index_cols(header)

        # Connect to database
        con = sqlite3.connect(self._settings['dbpath'])
        cur = con.cursor()

        # Parse table
        print('parsing table...')
        for i, row in enumerate(table):
            rownum = self._pre_header + 2 + i
            print(f"\n{Fore.MAGENTA}ROW {rownum}:{Fore.RESET}")

            refds = row[col['reference designator']]
            qty = row[col['quantity']]

            # Convert quantities to integers
            try:
                row[col['quantity']] = int(qty)
            except ValueError:
                pass

            # Highlight reference designators
            if refds:
                print('row contains loaded parts')

                # Find package type: pth or smd
                pkgtype = self._determine_pkgtype(header, row, col, con, cur)

                # Build tagged reference designators list
                refds_list = [item.strip() for item in refds.lstrip("'").split(',')]
                tagged_list = self._tag(refds_list, pkgtype)

                # replace the reference designators string
                row[col['reference designator']] = ','.join(tagged_list)
        print('\nfinished parsing table')

        # Close database
        con.close()

        # Generate typst document
        output_path = self._generate_typst_bom(pretable, header, table, col)
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
args = parser.parse_args()

app = App(args.BOM, args.pick, args.output, args.pre_header, args.yes)
app.settings_init(_settings_path)
app.database_init()
app.run()

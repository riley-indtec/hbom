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
        'part number': 'p(art)?[ /.]?n(um)?\\.?(ber)?',
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
        'pth': [],
        'smd': []
    },
    'smlgroups': ['CAP', 'RES', 'DIODE,TRNS', 'IC']
}

typ_indent = 2  # typst document indent


# Handle special characters for use in typst content
def typstify(msg):
    codes = ['*', '_', '`', '<', '>', '@', '=', '-', '+', '/', '$', "'", '"', '~', '#']
    msg = msg.replace('\\', '\\\\')
    for code in codes:
        msg = msg.replace(code, '\\' + code)
    return msg


def hbom_template(path, datestr, columns, pre_header, refd_col, highlight_colors):
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
  align: (x, y) => if x == {} and y != {} {{ left }} else {{ center }},
  fill: (_, y) => if y == {} {{ gray }},
  stroke: (_, y) => if y < {} {{ none }} else {{ 1pt + black }},
)

#set table.header(repeat: true)

#set table.cell(breakable: false)

#show table.cell.where(y: {}): strong

/* Reference designator highlighting */

{}/* Content */

#table(
  table.header(
'''.format(
        os.path.basename(path), datestr, typstify(os.path.abspath(path)), columns, refd_col,
        pre_header, pre_header, pre_header, pre_header, highlight_styling)


def sml_template(path, datestr, pca_model, pca_pn):
    return '''// {}
// Surface Mount Loading Table generated using hbom
// Created on {}

/* Document styling */

#set page(
  paper: "us-letter",
  margin: (top: .5in, rest: .2in),
  header: [{} #h(1fr) #datetime.today().display("[month]/[day]/[year]")],
  numbering: "1 of 1",
  footer-descent: -1em,
)

#set text(
  font: "DejaVu Sans Mono",
  size: 9pt,
)

#set heading(numbering: "1.")

#show heading.where(level: 1): it => {{
  set text(size: 11pt)
  underline(it)
}}

#set enum(numbering: "a.")

/* Templates */

#let initial-box = place(
  top + right,
  rect(width: 2.5in)[
    #v(1em)

    QTY Run:#box(width: 1fr, line(length: 100%))

    Initial:#box(width: 1fr, line(length: 100%))
  ]
)

#let sml-table(..component-groups) = {{
  set table.cell(breakable: false)
  let blank-row = table.cell(colspan: 10, stroke: none, v(1em))
  show table.cell.where(y: 0): strong
  show table.cell.where(y: 1): strong
  let counter = 0
  table(
    columns: 10,
    align: (x, y) => if x == 5 and y > 1 {{ left }} else {{ center }},
    fill: (x, y) => if x > 6 and y < 2 {{ gray }} else {{ none }},
    table.header(
      table.cell(rowspan: 2, align: horizon)[P/N],
      table.cell(rowspan: 2, align: horizon)[FD],
      table.cell(rowspan: 2, align: horizon)[Comp],
      table.cell(rowspan: 2, align: horizon)[FD TYPE],
      table.cell(rowspan: 2, align: horizon)[C.TYPE],
      table.cell(rowspan: 2, align: horizon)[Locations],
      table.cell(rowspan: 2, align: horizon)[QTY],
      table.cell(colspan: 3)[Component Change Verify],
      box(width: .75in)[P/N], box(width: .75in)[Value], [Initial],
    ),
    ..(for (i, group) in component-groups.pos().enumerate() {{
      let rows = for comp in group {{
        counter += 1
        (comp.pn, comp.fd, [#counter], comp.fdtype, comp.pkg, comp.ref, comp.qty, [], [], [])
      }}
      if i < component-groups.len() - 1 {{ (..rows, blank-row) }}
      else {{ rows }}
    }}),
  )
}}

/* Content */

*Model: {}*

*P/N: {}*

#initial-box
'''.format(
        os.path.basename(path), datestr, typstify(os.path.abspath(path)), typstify(pca_model),
        typstify(pca_pn))


# App object
class App:
    def __init__(self, bom_path, pick_path, output_path, pre_header, skip_prompts, sml_mode):
        self._bom_path = bom_path
        self._pick_path = pick_path
        self._output_path = output_path
        self._pre_header = pre_header
        self._sml_mode = sml_mode
        self._skip_prompts = skip_prompts

        self._settings = _default_settings.copy()
        self._index = {}  # for column indices

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
                CREATE TABLE pkgtypemem (
                    id TEXT NOT NULL UNIQUE,
                    desc TEXT,
                    pkg TEXT,
                    pkgtype TEXT
                )''')
            print(f"created new table '{Fore.YELLOW}pkgmem{Fore.RESET}' in database")
        except sqlite3.OperationalError:
            pass

        # Create feedmem table
        try:
            cur.execute('''
                CREATE TABLE feedmem (
                    pkg TEXT NOT NULL UNIQUE,
                    feedtype TEXT
                )''')
            print(f"created new table '{Fore.YELLOW}feedmem{Fore.RESET}' in database")
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
            ans = input(f"{prompt} {Fore.GREEN}[0-{n - 1}]{Fore.RESET} ('q' to quit): ")
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

    def _open_prompt(self, prompt):
        ans = input(f"{prompt} ('none' to skip) ('q' to quit): ")
        if ans.lower() in ['q', 'quit']:
            self.close()
        if ans.lower() == 'none':
            return None
        return ans

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

    def _index_columns(self, header, keys):
        print('selecting columns...')

        # Automatically locate columns using header names
        for j, name in enumerate(header):
            patterns = self._settings['headerpatterns']
            for key in keys:
                if key in patterns:
                    if re.fullmatch(patterns[key], name, re.IGNORECASE):
                        if key not in self._index:
                            self._index[key] = j
                            print(f"found the {key} column: [{Fore.YELLOW}{name}{Fore.RESET}]")

        # Manually select any required columns not already found
        for key in keys:
            if key not in self._index:
                print(f"couldn't find the {key} column")
                for j, name in enumerate(header):
                    print(f'{Fore.GREEN}{j}{Fore.RESET}: [{Fore.YELLOW}{name}{Fore.RESET}]')
                self._index[key] = self._num_prompt(f'Select the {key} column', len(header))
                print(f'selected [{Fore.YELLOW}{header[self._index[key]]}{Fore.RESET}]')

        print('finished selecting columns')

    def _determine_pkgtype(self, header, row, con, cur):
        print('determining package type...')
        pid = row[self._index['product identifier']]
        desc = row[self._index['description']]

        # First, try searching the description
        if desc:
            for pkgtype, patterns in self._settings['pkgpatterns'].items():
                for pattern in patterns + [pkgtype]:
                    if re.search('(^|\\s|,)' + pattern + '($|\\s|,)', desc, re.IGNORECASE):
                        print("description '{}' matches {} package type".format(
                            Fore.YELLOW + desc + Fore.RESET,
                            Fore.YELLOW + pkgtype + Fore.RESET))
                        return pkgtype

        # Next, try the database
        if pid:
            result = cur.execute('''
                SELECT pkgtype
                FROM pkgmem
                WHERE id = '{}' AND pkgtype IS NOT NULL
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
                    try:
                        cur.execute('''
                            INSERT INTO pkgmem (id, desc, pkgtype)
                            VALUES ('{}', '{}', '{}')
                            '''.format(pid, desc, pkgtype))
                    except sqlite3.IntegrityError:
                        cur.execute('''
                            UPDATE pkgmem
                            SET pkgtype = '{}'
                            WHERE id = '{}'
                            '''.format(pkgtype, pid))
                    con.commit()
                    print("saved '{}' | '{}' as {} to database".format(
                        Fore.YELLOW + pid + Fore.RESET,
                        Fore.YELLOW + desc + Fore.RESET,
                        Fore.YELLOW + pkgtype + Fore.RESET))
        return pkgtype

    def _determine_pkg(self, header, row, con, cur):
        print('determining package...')
        pid = row[self._index['product identifier']]
        desc = row[self._index['description']]

        # First, try searching the description
        if desc:
            for pkgtype, patterns in self._settings['pkgpatterns'].items():
                for pattern in patterns:
                    m = re.search('(^|\\s|,)' + pattern + '($|\\s|,)', desc, re.IGNORECASE)
                    if m:
                        pkg = m.group(0)
                        pkg = re.sub("^[\\s,]+", '', pkg)
                        pkg = re.sub('[\\s,]+$', '', pkg)
                        print("found package '{}' in the description".format(
                            Fore.YELLOW + pkg + Fore.RESET))
                        return pkg

        # Next, try the database
        if pid:
            result = cur.execute('''
                SELECT pkg
                FROM pkgmem
                WHERE id = '{}' AND pkg IS NOT NULL
                '''.format(pid)).fetchone()
            if result:
                pkg = result[0]
                if pkg:
                    print("found '{}' in database as '{}' package".format(
                        Fore.YELLOW + pid + Fore.RESET,
                        Fore.YELLOW + pkg + Fore.RESET))
                    return pkg

        # Lastly, prompt the user
        pkg = None
        print('unable to determine package for the following:')
        for key, value in zip(header, row):
            print(f'[{key}]: {Fore.YELLOW}{value}{Fore.RESET}')
        if not self._skip_prompts:
            pkg = self._open_prompt(
                "Enter a package for PID: '{}', DESC: '{}'".format(
                    Fore.YELLOW + pid + Fore.RESET,
                    Fore.YELLOW + desc + Fore.RESET))
            print(f"entered '{Fore.YELLOW}{pkg}{Fore.RESET}'")

            # Save to database
            if pkg is not None and pid:
                prompt = "Save '{}' as '{}' to database?".format(
                    Fore.YELLOW + pid + Fore.RESET,
                    Fore.YELLOW + pkg + Fore.RESET)
                if self._bool_prompt(prompt):
                    try:
                        cur.execute('''
                            INSERT INTO pkgmem (id, desc, pkg)
                            VALUES ('{}', '{}', '{}')
                            '''.format(pid, desc, pkg))
                    except sqlite3.IntegrityError:
                        cur.execute('''
                            UPDATE pkgmem
                            SET pkg = '{}'
                            WHERE id = '{}'
                            '''.format(pkg, pid))
                    con.commit()
                    print("saved '{}' | '{}' as '{}' to database".format(
                        Fore.YELLOW + pid + Fore.RESET,
                        Fore.YELLOW + desc + Fore.RESET,
                        Fore.YELLOW + pkg + Fore.RESET))
        return pkg

    def _determine_faces(self, component_refds):
        print('determining faces...')
        faces = []
        repeat = None

        for i, refd in enumerate(component_refds):
            face = None
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
                        if re.search('(^|\\s)' + re.escape(refd) + '\\s', line):
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
                    if face and i == 0 and len(component_refds) > 1:
                        prompt = 'Are {} all on the same side?'.format(
                            ','.join([Fore.YELLOW + refd + Fore.RESET for refd in component_refds]))
                        if self._bool_prompt(prompt):
                            repeat = face
                            print("repeating '{}' for all ref. designators in the row".format(
                                Fore.YELLOW + face + Fore.RESET))

            # Append reference designator and face
            faces.append(face)

        print('finished determining faces')
        return faces

    def _determine_group(self, header, row, groups):
        print('determining group...')
        pid = row[self._index['product identifier']]
        desc = row[self._index['description']]

        # Try searching the description
        if desc:
            for group in groups:
                m = re.search('^' + group + '(\\s|,)', desc, re.IGNORECASE)
                if m:
                    print("found group '{}' in the description".format(
                        Fore.YELLOW + group.lower() + Fore.RESET))
                    return group.lower()

        # Otherwise, prompt the user
        group = None
        print('unable to the component group for the following:')
        for key, value in zip(header, row):
            print(f'[{key}]: {Fore.YELLOW}{value}{Fore.RESET}')
        if not self._skip_prompts:
            group = self._options_prompt(
                "Enter a group for PID: '{}', DESC: '{}'".format(
                    Fore.YELLOW + pid + Fore.RESET,
                    Fore.YELLOW + desc + Fore.RESET),
                groups)
            print(f'selected {Fore.YELLOW}{group}{Fore.RESET}')
        return group

    def _determine_feedtype(self, pkg, con, cur):
        print('determining feed type...')

        # Try the database
        result = cur.execute('''
            SELECT feedtype
            FROM feedmem
            WHERE pkg = '{}'
            '''.format(pkg)).fetchone()
        if result:
            feedtype = result[0]
            if pkg:
                print("found feed type '{}' for package '{}' in database".format(
                    Fore.YELLOW + feedtype + Fore.RESET,
                    Fore.YELLOW + pkg + Fore.RESET))
                return feedtype

        # Otherwise, prompt the user
        feedtype = None
        print(f"unable to determine feedtype for '{Fore.YELLOW}{pkg}{Fore.RESET}' package")
        if not self._skip_prompts:
            feedtype = self._open_prompt(
                f"Enter a feed type for '{Fore.YELLOW}{pkg}{Fore.RESET}' package")
            print(f"entered '{Fore.YELLOW}{feedtype}{Fore.RESET}'")

            # Save to database
            if feedtype is not None:
                prompt = "Save feed type '{}' for package '{}' to database?".format(
                    Fore.YELLOW + feedtype + Fore.RESET,
                    Fore.YELLOW + pkg + Fore.RESET)
                if self._bool_prompt(prompt):
                    cur.execute('''
                        INSERT INTO feedmem
                        VALUES ('{}', '{}')
                        '''.format(pkg, feedtype))
                    con.commit()
                    print("saved '{}' | '{}' to database".format(
                        Fore.YELLOW + pkg + Fore.RESET,
                        Fore.YELLOW + feedtype + Fore.RESET))
        return feedtype

    def _pca_model_prompt(self, pretable):
        print('PCA model...')

        # Select from the pretable
        if pretable:
            options = []
            for row in pretable:
                for item in row:
                    if item:
                        options.append(item.lstrip("'"))
            for i, option in enumerate(options):
                print(f"{Fore.GREEN}{i + 1}{Fore.RESET}: '{Fore.YELLOW}{option}{Fore.RESET}'")
            ans = self._num_prompt(
                f"Select the PCA model ('{Fore.GREEN}0{Fore.RESET}' to manually enter)",
                len(options) + 1)
            if ans != 0:
                model = options[ans - 1]
                print(f'selected [{Fore.YELLOW}{model}{Fore.RESET}]')
                return model

        # Otherwise, prompt the user
        model = None
        if not self._skip_prompts:
            model = self._open_prompt("Manually enter PCA model")
            print(f"entered '{Fore.YELLOW}{model}{Fore.RESET}'")
        return model

    def _pca_pn_prompt(self, pretable):
        print('PCA part number...')
        # Select from the pretable
        if pretable:
            options = []
            for row in pretable:
                for item in row:
                    if item:
                        options.append(item.lstrip("'"))
            for i, option in enumerate(options):
                print(f"{Fore.GREEN}{i + 1}{Fore.RESET}: '{Fore.YELLOW}{option}{Fore.RESET}'")
            ans = self._num_prompt(
                f"Select the PCA part number('{Fore.GREEN}0{Fore.RESET}' to manually enter)",
                len(options) + 1)
            if ans != 0:
                pn = options[ans - 1]
                print(f'selected [{Fore.YELLOW}{pn}{Fore.RESET}]')
                return pn

        # Otherwise, prompt the user
        pn = None
        if not self._skip_prompts:
            pn = self._open_prompt("Manually enter the PCA part number")
            print(f"entered '{Fore.YELLOW}{pn}{Fore.RESET}'")
        return pn

    def _generate_typst_bom(self, pretable, header, table):
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
            f.write(hbom_template(output_path, datetime.now().strftime("%d %b %Y"), len(header),
                    self._pre_header, self._index['reference designator'],
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
                    if j != self._index['reference designator'] and type(item) is str:
                        item = typstify(item)
                    f.write(f' [{item}],')
                f.write('\n')
            f.write(')\n')

        print(f"finished writing to '{Fore.CYAN}{output_path}{Fore.RESET}'")
        return output_path

    def _generate_typst_sml(self, sml_components, pca_model, pca_pn):
        output_path = self._output_path
        if output_path is None:
            output_path = os.path.splitext(
                os.path.basename(self._bom_path))[0] + '_sml.typ'

        print(f"writing to '{Fore.CYAN}{output_path}{Fore.RESET}'...")
        dir = os.path.dirname(output_path)
        if dir:
            os.makedirs(dir, exist_ok=True)
        with open(output_path, 'w') as f:
            # Template
            model = pca_model if pca_model else ''
            pn = pca_pn if pca_pn else ''
            f.write(sml_template(output_path, datetime.now().strftime("%d %b %Y"), model, pn))

            # Create tables
            for face in ['top', 'bot']:
                components = sml_components[face]
                if not components:
                    continue
                f.write(f'\n= Set-up for {face.upper()} side\n')

                # Generate the table
                f.write('\n#sml-table(\n')
                for group in self._settings['smlgroups'] + [None]:
                    if group not in components:
                        continue
                    f.write(typ_indent * ' ' + '(\n')
                    for component in components[group]:
                        typ_keyvals = []
                        for key, value in component.items():
                            if value is None:
                                typ_keyvals.append(f'{key}: []')
                            elif type(value) is list:
                                fval = ','.join([f'#box[{x}]' for x in value])
                                typ_keyvals.append(f'{key}: [{fval}]')
                            elif type(value) is str:
                                typ_keyvals.append(f'{key}: [{typstify(value)}]')
                            else:
                                typ_keyvals.append(f'{key}: [{value}]')
                        f.write(2 * typ_indent * ' ' + '(' + ', '.join(typ_keyvals) + '),\n')
                    f.write(typ_indent * ' ' + '),\n')
                f.write(')\n')
                f.write('\n= Temp. Profile:\n')

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

        if self._sml_mode:
            # Index the required columns
            self._index_columns(header, [
                'description',
                'part number',
                'product identifier',
                'reference designator'])

            # Connect to database
            con = sqlite3.connect(self._settings['dbpath'])
            cur = con.cursor()

            # Parse table
            print('parsing table...')
            sml_components = {face: {} for face in ['top', 'bot']}
            for i, row in enumerate(table):
                rownum = self._pre_header + 2 + i
                print(f"\n{Fore.MAGENTA}ROW {rownum}:{Fore.RESET}")

                refds = row[self._index['reference designator']]
                if not refds:
                    continue
                print('row contains loaded parts')

                # Determine package type: pth or smd
                pkgtype = self._determine_pkgtype(header, row, con, cur)
                if pkgtype != 'smd':
                    print('not smd')
                    continue

                # Determine package
                pkg = self._determine_pkg(header, row, con, cur)

                # Determine faces
                refds_list = [item.strip() for item in refds.lstrip("'").split(',')]
                faces = self._determine_faces(refds_list) if pkgtype else [
                    None for refd in refds_list]

                has_faces = False
                refds_per_face = {face: [] for face in ['top', 'bot']}
                for refd, face in zip(refds_list, faces):
                    if face is None:
                        continue
                    refds_per_face[face].append(refd)
                    has_faces = True
                if not has_faces:
                    print('not located on any face')
                    continue

                # Determine component group
                groups = []
                for smlgroup in self._settings['smlgroups']:
                    groups += smlgroup.split(',')
                group = self._determine_group(header, row, groups)

                # Convert back to sml grouping
                if group:
                    for smlgroup in self._settings['smlgroups']:
                        m = re.search('(^|,)' + group + '($|,)', smlgroup, re.IGNORECASE)
                        if m:
                            group = smlgroup
                            print(f"grouping with '{Fore.YELLOW}{group}{Fore.RESET}'")
                            break

                feedtype = None
                if pkg:
                    feedtype = self._determine_feedtype(pkg, con, cur)

                for face in ['top', 'bot']:
                    if len(refds_per_face[face]) == 0:
                        continue
                    if group not in sml_components[face]:
                        sml_components[face][group] = []
                    pn = row[self._index['part number']]
                    sml_components[face][group].append(
                        {
                            'pn': pn,
                            'fd': None,
                            'fdtype': feedtype,
                            'pkg': pkg,
                            'ref': refds_per_face[face],
                            'qty': len(refds_per_face[face])
                        })

            print('\nfinished parsing table')

            # Close database
            con.close()

            # Get PCA model
            pca_model = self._pca_model_prompt(pretable)

            # Get PCA part number
            pca_pn = self._pca_pn_prompt(pretable)

            # Generate typst document
            output_path = self._generate_typst_sml(sml_components, pca_model, pca_pn)
            print('done')
            return output_path
        else:
            # Index the required columns
            self._index_columns(header, [
                'description',
                'product identifier',
                'reference designator'])

            # Connect to database
            con = sqlite3.connect(self._settings['dbpath'])
            cur = con.cursor()

            # Parse table
            print('parsing table...')
            for i, row in enumerate(table):
                rownum = self._pre_header + 2 + i
                print(f"\n{Fore.MAGENTA}ROW {rownum}:{Fore.RESET}")

                refds = row[self._index['reference designator']]

                # Highlight reference designators
                if refds:
                    print('row contains loaded parts')

                    # Find package type: pth or smd
                    pkgtype = self._determine_pkgtype(header, row, con, cur)

                    # Build tagged reference designators list
                    refds_list = [item.strip() for item in refds.lstrip("'").split(',')]
                    faces = self._determine_faces(refds_list) if pkgtype else [
                        None for refd in refds_list]
                    tagged = []
                    for refd, face in zip(refds_list, faces):
                        if pkgtype and face:
                            tagged.append(f'#box[{refd}]<{pkgtype}{face}>')
                            print("tagged '{}' with {}".format(
                                Fore.YELLOW + refd + Fore.RESET,
                                Fore.YELLOW + f'<{pkgtype}{face}>' + Fore.RESET))
                        else:
                            tagged.append(f'#box[{refd}]')
                            print(f"'{Fore.YELLOW}{refd}{Fore.RESET}' is untagged")

                    # replace the reference designators string
                    row[self._index['reference designator']] = ','.join(tagged)
            print('\nfinished parsing table')

            # Close database
            con.close()

            # Generate typst document
            output_path = self._generate_typst_bom(pretable, header, table)
            print('done')
            return output_path

        return None


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
    '--sml',
    action='store_true',
    help='generate SML document')
args = parser.parse_args()

app = App(args.BOM, args.pick, args.output, args.pre_header, args.yes, args.sml)
app.settings_init(_settings_path)
app.database_init()
app.run()

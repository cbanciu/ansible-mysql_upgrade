"""
upgrade_mysql_config.py
~~~~~~~~~~~~~~~~~~~~~~

Update a my.cnf server config by removing or rewriting deprecated options.

:author: Andrew Garner <andrew.garner@rackspace.com>
:version: 1.0.1
"""

import os, sys
import re
import difflib
import optparse
import logging
import glob
from string import Template

LOG = logging.getLogger(__name__)

# options we might validly see multiple times in a mysql section
# used to not warn so harshly about duplicates in this case
multi_valued_options = (
    'binlog-do-db',
    'binlog-ignore-db',
    'replicate-do-db',
    'replicate-ignore-db',
    'replicate-do-table',
    'replicate-ignore-table',
    'replicate-wild-do-table',
    'replicate-wild-ignore-table',
)

# basic parsing helper methods
# borrowed from holland.lib.mysql's option parsing package
def remove_inline_comment(value):
    """Remove a MySQL inline comment from an option file line"""
    escaped = False
    quote = None
    for idx, char in enumerate(value):
        if char in ('"', "'") and not escaped:
            if not quote:
                quote = char
            elif quote == char:
                quote = None
        if not quote and char == '#':
            return value[0:idx], value[idx:]
        escaped = (quote and char == '\\' and not escaped)
    return value, ''

def unpack_option_value(value):
    """Process an option value according to MySQL's syntax rules"""
    value, comment = remove_inline_comment(value)
    value = value.strip()
    return value, comment

def resolve_option(item):
    """Expand an option prefix to the full name of the option"""
    known = [
        u'host',
        u'password',
        u'port',
        u'socket',
        u'user',
    ]
    candidates = [key for key in known if key.startswith(item)]

    if len(candidates) > 1:
        # mimic MySQL's error message
        raise ParseError("ambiguous option '%s' (%s)" %
                         (item, ','.join(candidates)))
    elif not candidates:
        return item

    return candidates[0]

SV_CRE = re.compile(r'(?P<sv>set[-_]variable\s*=\s*)(?P<value>.*)')

def sanitize(line, lineno):
    match = SV_CRE.match(line)
    if match:
        value = match.group('value')
        LOG.info("Rewrote obsolete syntax %r to %r on line %d", line.rstrip(), value.rstrip(), lineno)
        return value
    return line

KV_CRE = re.compile(r'\s*(?P<key>[^=\s]+?)\s*(?:=\s*(?P<value>.*))?$')

def parse_option(line):
    """Process a key/value directive according to MySQL syntax rules

    :returns: tuple if line is a valid key/value pair otherwise returns None
              If this is a bare option such as 'no-auto-rehash' the value
              element of the key/value tuple will be None
    """
    match = KV_CRE.match(line)
    if match:
        key, value = match.group('key', 'value')
        if value:
            value, inline_comment = unpack_option_value(value)
        else:
            key, inline_comment = remove_inline_comment(key)
        key = resolve_option(key)
        return key, value, inline_comment
    return None

## Rewrite support
class RewriteRule(object):
    """Rewrite an option into zero or more options

    :attr options: list of lines to rewrite some option into

    Each option may have a ${value} or ${key} macro which
    will be replaced by the original value or option name
    (respectively)

    :attr reason: an optional reason that will be logged when
                  rewriting an option

    Example remove 'skip-innodb' option:

    >> noop_rule = RewriteRule([], reason='Stupid option.')
    >> for line in noop_rule.rewrite('skip-innodb', None):
        print line
    [INFO] Rewriting option '%s'.  Reason: Stupid option.
    >>
    """
    def __init__(self, options=None, reason='unknown'):
        self.options = options
        self.reason = reason

    def __call__(self, key, value):
        if self.options:
            action = 'Rewriting'
        else:
            action = 'Removing'
        LOG.info("%s option '%s'. Reason: %s", action, key, self.reason)

        for option in self.options:
            yield Template(option).safe_substitute(key=key, value=value)

class SlowLogRewriteRule(RewriteRule):
    def __call__(self, key, value):
        self.options = [
            'slow-query-log = 1',
            'slow-query-log-file = ${value}',
            'log-slow-slave-statements',
        ]
        if value is None:
            # don't output slow-query-log-file if a value wasn't previously set
            # we'll default to the same host_name-slow.log
            self.options.remove('slow-query-log-file = ${value}')
        for line in super(SlowLogRewriteRule, self).__call__(key, value):
            yield line

class OptionRewriter(object):
    """Base OptionRewriter

    :attr rules: table (dict) of options -> RewriteRule

    """
    rules = {
    }

    @classmethod
    def rewrite(cls, key, value=None):
        """Rewrite an option according to a rule table

        :param key: option to rewrite
        :param value: original value for the option

        :returns: returns rewritten option list
        """
        try:
            rule = cls.rules[key]
        except KeyError:
            LOG.debug("No rule to rewrite '%s'", key)
            return None

        return [line for line in rule(key, value)]

class MySQL51OptionRewriter(OptionRewriter):
    """Option Rewriter for MySQL 5.1 options"""

    rules = {
        'default-character-set' : RewriteRule([
            'character-set-server = ${value}',
        ],
        reason="Deprecated in MySQL 5.0 in favor of character-set-server"),
        'default-collation' : RewriteRule([
            'collation-server = ${value}',
        ],
        reason="Deprecated in MySQL 4.1.3 in favor of collation-server"),
    'default-table-type' : RewriteRule([
        'default-storage-engine = ${value}',
    ],
    reason="Deprecated in MySQL 5.0 in favor of default-storage-engine"),
        'log-slow-queries' : SlowLogRewriteRule(
            reason='Logging options changed in MySQL 5.1'
        ),
        'table-cache' : RewriteRule([
            'table-open-cache = ${value}',
            'table-definition-cache = ${value}',
        ], reason='Table cache options changed in MySQL 5.1'),
        # null rules (completely removes from output)
        'enable-pstack'         : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.54'),
        'log-long-format'       : RewriteRule([
        ], reason="Deprecated in MySQL 4.1"),
        'log-short-format'      : RewriteRule([
        ], reason="Deprecated in MySQL 4.1. This option now does nothing."),
        'master-connect-retry'  : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.17. Removed in 5.5'),
        'master-host'           : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.17. Removed in 5.5'),
        'master-password'       : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.17. Removed in 5.5'),
        'master-port'           : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.17. Removed in 5.5'),
        'master-user'           : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.17. Removed in 5.5'),
        'master-ssl'            : RewriteRule([
        ], reason='Deprecated in MySQL 5.1.17. Removed in 5.5'),
        'safe-mode'             : RewriteRule([
        ], reason="Deprecated in MySQL 5.0"),
        'safe-show-database'    : RewriteRule([
        ], reason="Deprecated in MySQL 4.0.2"),
        'skip-locking'          : RewriteRule([
        ], reason='Deprecated in MySQL 4.0.3. Removed in 5.5'),
        'skip-external-locking' : RewriteRule([
        ], reason='Default behavior in MySQL 4.1+'),
        'skip-bdb'              : RewriteRule([
        ], reason='Removed in MySQL 5.1.11'),
        'skip-innodb'           : RewriteRule([
        ], reason='Default storage engine in 5.5'),
    'skip-thread-priority'  : RewriteRule([
    ], reason="Deprecated in MySQL 5.1.29"),
    }

class MySQL55OptionRewriter(MySQL51OptionRewriter):
    rules = dict(MySQL51OptionRewriter.rules)
    rules['one-thread'] = RewriteRule([
        '--thread-handling=no-threads',
    ], reason="Deprecated and removed in MySQL 5.6")

class MySQL56OptionRewriter(MySQL55OptionRewriter):
    rules = dict(MySQL55OptionRewriter.rules)

class MySQL57OptionRewriter(MySQL56OptionRewriter):
    rules = dict(MySQL55OptionRewriter.rules)

def parse(path):
    """Parse an iterable of lines into a list and option mapping

    :returns: tuple list, option-mapping

    option-mapping is a table of option-name -> list of offsets
    where list-of-offsets notes where in the list of lines the option
    can be found.  If an option is repeated multiple times it will
    have multiple entries in the option-mapping table.
    """
    # remaining !included .cnf to worry about
    paths = [path]


    while paths:
        iterable = open(paths.pop(0), 'rb')
        section = None
        lines = []
        # map interesting options -> offsets in lines[]
        keys = {}
        for idx, line in enumerate(iterable):
            lines.append(line)
            # trim righ-trailing whitespace
            # (preserve original line)
            _line = sanitize(line, idx+1).rstrip()
            if not _line:
                # skip blank lines
                continue
            if _line.startswith('['):
                section = _line[1:-1]
                continue
    
            if _line.startswith('#'):
                continue
    
            if _line.startswith('!include '):
                paths.append(_line.split(None, 2)[-1])
                continue
    
            if _line.startswith('!includedir '):
                _path = os.path.join(_line.split(None, 2)[-1], '*.cnf')
                paths.extend(glob.glob(_path))
                continue
    
            if section != 'mysqld':
                LOG.debug("Ignoring section [%s] options %s on line %d",
                          section, _line, idx+1)
                continue
            # must be an option or a syntax error
            key, value, inline_comment = parse_option(_line)
    
            # normalize key
            # XXX: handle prefix-values (e.g. key-buffer -> key-buffer-size)
            key = key.replace('_', '-')
    
            keys.setdefault(key, [])
            keys[key].append((idx, value, _line))
    
        yield iterable.name, lines, keys

def upgrade_config(path, rewriter):
    for path, lines, keys in parse(path):
        purge_list = []
        pending = {}
    
        for key, idx_list in keys.iteritems():
            if len(idx_list) > 1 and key != 'set-variable':
                LOG.warning("Duplicate options for '%s'", key)
                for idx, _, _ in idx_list:
                    LOG.warning("  - %d:%s", idx+1, lines[idx].rstrip())
    
            for idx, value, line in idx_list:
                options = rewriter.rewrite(key, value)
                if options is not None:
                    # push new options into pending
                    pending.setdefault(idx, [])
                    pending[idx].extend(options)
                elif line != lines[idx]:
                    pending.setdefault(idx, [line])
    
        result = []
        for idx, line in enumerate(lines):
            # skip lines we should completely purge
            if idx in purge_list:
                LOG.warning("Removing option %d:%s", idx, line.rstrip())
                continue
            # replace lines we rewrite
            if idx in pending:
                LOG.debug("Rewriting %d:%s", idx, line.rstrip())
                for line in pending[idx]:
                    LOG.debug("  + %s", line)
                    result.append(line + '\n')
            else:
                # otherwise just output the original line
                result.append(line)
        yield path, lines, result

# backport from py2.7.  patched to support paths relative to /
def relpath(path, start=os.curdir):
    """Return a relative version of a path"""
    sep = os.sep
    curdir = os.curdir
    pardir = os.pardir
    commonprefix = os.path.commonprefix
    abspath = os.path.abspath
    join = os.path.join

    if not path:
        raise ValueError("no path specified")

    start_list = [x for x in abspath(start).split(sep) if x]
    path_list = [x for x in abspath(path).split(sep) if x]

    # Work out how much of the filepath is shared by start and path.
    i = len(commonprefix([start_list, path_list]))

    rel_list = [pardir] * (len(start_list)-i) + path_list[i:]
    if not rel_list:
        return curdir
    return join(*rel_list)

def main(args=None):
    parser = optparse.OptionParser()
    parser.add_option('-c', '--config', metavar='<path>',
                      default='/etc/my.cnf',
                      help="my.cnf file to parse (default: %default)")
    parser.add_option('-t', '--target', metavar='<mysql-version>',
                      choices=('5.1', '5.5', '5.6', '5.7'),
                      default='5.1',
                      help=("MySQL version to target the option file"
                            "(default: %default)")
                    )
    parser.add_option('-l', '--log-level', metavar='<log-level>',
                      choices=('debug','info','warning','error','fatal'),
                      default='info',
                      help=("Log level to write information messages to")
                     )
    parser.add_option('-p', '--patch', action='store_true',
                      default=False,
                      help=("Output a unified diff rather than an "
                            "entire config file (default: %default)")
                    )
    options, args = parser.parse_args(args)

    log_level = logging._levelNames[options.log_level.upper()]
    logging.basicConfig(level=log_level,
                        format='[%(levelname)s] %(message)s')

    if options.target == '5.1':
        rewriter = MySQL51OptionRewriter
    else:
        rewriter = MySQL55OptionRewriter

    for path, orig, modified in upgrade_config(options.config, rewriter):
        if options.patch:
            # make patch file names pretty
            from_file = relpath(os.path.abspath(path), '/')
            to_file = os.path.join('b', from_file)
            from_file = os.path.join('a', from_file)
            print ''.join(difflib.unified_diff(orig, modified, from_file, to_file))
        else:
            print ''.join(modified)
    return 0

if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/python

#
# Perform an online ALTER TABLE
#
# Released under the BSD license
#
# Copyright (c) 2008, Shlomi Noach
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
#     * Neither the name of the organization nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import getpass
import MySQLdb
import time
import re
from optparse import OptionParser

def parse_options():
    parser = OptionParser()
    parser.add_option("-u", "--user", dest="user", default="", help="MySQL user")
    parser.add_option("-H", "--host", dest="host", default="localhost", help="MySQL host (default: localhost)")
    parser.add_option("-p", "--password", dest="password", default="", help="MySQL password")
    parser.add_option("--ask-pass", action="store_true", dest="prompt_password", help="Prompt for password")
    parser.add_option("-P", "--port", dest="port", type="int", default="3306", help="TCP/IP port (default: 3306)")
    parser.add_option("-S", "--socket", dest="socket", default="/var/run/mysqld/mysql.sock", help="MySQL socket file. Only applies when host is localhost")
    parser.add_option("", "--defaults-file", dest="defaults_file", default="", help="Read from MySQL configuration file. Overrides all other options")
    parser.add_option("-d", "--database", dest="database", help="Database name (required unless table is fully qualified)")
    parser.add_option("-t", "--table", dest="table", help="Table to alter (optionally fully qualified)")
    parser.add_option("-g", "--ghost", dest="ghost", help="Table name to serve as ghost. This table will be created and synchronized with the original table")
    parser.add_option("-a", "--alter", dest="alter_statement", help="Comma delimited ALTER statement details, excluding the 'ALTER TABLE t' itself")
    parser.add_option("-c", "--chunk-size", dest="chunk_size", type="int", default=1000, help="Number of rows to act on in chunks. Default: 1000")
    parser.add_option("-l", "--lock-chunks", action="store_true", dest="lock_chunks", default=False, help="Use LOCK TABLES for each chunk")
    parser.add_option("--sleep", dest="sleep_millis", type="int", default=0, help="Number of milliseconds to sleep between chunks. Default: 0")
    parser.add_option("--cleanup", dest="cleanup", action="store_true", default=False, help="Remove custom triggers, ghost table from possible previous runs")
    parser.add_option("-v", "--verbose", dest="verbose", action="store_true", default=True, help="Print user friendly messages")
    parser.add_option("-q", "--quiet", dest="verbose", action="store_false", help="Quiet mode, do not verbose")
    return parser.parse_args()

def verbose(message):
    if options.verbose:
        print "-- %s" % message

def print_error(message):
    print "-- ERROR: %s" % message

def open_connection():
    verbose("Connecting to MySQL")
    if options.defaults_file:
        conn = MySQLdb.connect(read_default_file = options.defaults_file)
    else:
        if options.prompt_password:
            password=getpass.getpass()
        else:
            password=options.password
        conn = MySQLdb.connect(
            host = options.host,
            user = options.user,
            passwd = password,
            port = options.port,
            db = database_name,
            unix_socket = options.socket)
    return conn;


def act_query(query):
    """
    Run the given query, commit changes
    """
    if reuse_conn:
        connection = conn
    else:
        connection = open_connection()
    cursor = connection.cursor()
    #print query
    cursor.execute(query)
    cursor.close()
    connection.commit()
    if not reuse_conn:
        connection.close()


def get_row(query):
    if reuse_conn:
        connection = conn
    else:
        connection = open_connection()
    cursor = connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(query)
    row = cursor.fetchone()

    cursor.close()
    if not reuse_conn:
        connection.close()
    return row


def get_rows(query):
    if reuse_conn:
        connection = conn
    else:
        connection = open_connection()
    cursor = connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(query)
    rows = cursor.fetchall()

    cursor.close()
    if not reuse_conn:
        connection.close()
    return rows


def get_session_variable_value(session_variable_name):

    query = """
        SELECT @%s AS %s
        """ % (session_variable_name, session_variable_name)
    row = get_row(query)
    session_variable_value = row[session_variable_name]

    return session_variable_value


def get_possible_unique_key_columns(read_table_name):
    """
    Return the columns with unique keys which are acceptable by this utility
    """
    verbose("Checking for UNIQUE columns on %s.%s, by which to chunk" % (database_name, read_table_name))
    query = """
        SELECT COLUMNS.TABLE_SCHEMA, COLUMNS.TABLE_NAME, COLUMNS.COLUMN_NAME, UNIQUES.INDEX_NAME, COLUMNS.DATA_TYPE, COLUMNS.CHARACTER_SET_NAME
        FROM INFORMATION_SCHEMA.COLUMNS INNER JOIN (
          SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME) AS COLUMN_NAME
          FROM INFORMATION_SCHEMA.STATISTICS
          WHERE NON_UNIQUE=0
          GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME
          HAVING COUNT(*)=1
        ) AS UNIQUES
        ON (
          COLUMNS.TABLE_SCHEMA = UNIQUES.TABLE_SCHEMA AND
          COLUMNS.TABLE_NAME = UNIQUES.TABLE_NAME AND
          COLUMNS.COLUMN_NAME = UNIQUES.COLUMN_NAME
        )
        WHERE
          COLUMNS.TABLE_SCHEMA = '%s'
          AND COLUMNS.TABLE_NAME = '%s'
        ORDER BY
          COLUMNS.TABLE_SCHEMA, COLUMNS.TABLE_NAME,
          CASE UNIQUES.INDEX_NAME
            WHEN 'PRIMARY' THEN 0
            ELSE 1
          END,
          CASE IFNULL(CHARACTER_SET_NAME, '')
              WHEN '' THEN 0
              ELSE 1
          END,
          CASE DATA_TYPE
            WHEN 'tinyint' THEN 0
            WHEN 'smallint' THEN 1
            WHEN 'int' THEN 2
            WHEN 'bigint' THEN 3
            ELSE 100
          END
        """ % (database_name, read_table_name)
    rows = get_rows(query)
    return rows


def get_possible_unique_key_column_names(read_table_name):
    """
    Return the names of columns with acceptable unique keys
    """
    rows = get_possible_unique_key_columns(read_table_name)
    possible_unique_key_column_names = [row["COLUMN_NAME"].lower() for row in rows]

    verbose("Possible UNIQUE KEY column names in %s.%s:" % (database_name, read_table_name))
    for possible_unique_key_column_name in possible_unique_key_column_names:
        verbose("- %s" % possible_unique_key_column_name)

    return set(possible_unique_key_column_names)


def get_shared_unique_key_column(shared_column_names):
    """
    Return the column name (lower case) of the AUTO_INCREMENT column in the given table,
    or None if no such column is found.
    """

    rows = get_possible_unique_key_columns(original_table_name)

    unique_key_column_name = None
    unique_key_type = None
    if rows:
        verbose("- Found following possible columns:")
        for row in rows:
            column_name = row["COLUMN_NAME"].lower()
            if column_name in shared_column_names:
                column_data_type = row["DATA_TYPE"].lower()
                character_set_name = row["CHARACTER_SET_NAME"]
                verbose("- %s (%s)" % (column_name, column_data_type))
                if unique_key_column_name is None:
                    unique_key_column_name = column_name
                    if character_set_name is not None:
                        unique_key_type = "text"
                    elif column_data_type in ["tinyint", "smallint", "int", "bigint"]:
                        unique_key_type = "integer"
                    elif column_data_type in ["time", "date", "timestamp", "datetime"]:
                        unique_key_type = "temporal"

        verbose("Chosen unique column is '%s'" % unique_key_column_name)

    return unique_key_column_name, unique_key_type


def get_auto_increment_column(read_table_name):
    """
    Return the column name (lower case) of the AUTO_INCREMENT column in the given table,
    or None if no such column is found.
    """
    unique_key_column_name = None

    query = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='%s'
            AND TABLE_NAME='%s'
            AND LOCATE('auto_increment', EXTRA) > 0
        """ % (database_name, read_table_name)
    row = get_row(query)

    if row:
        unique_key_column_name = row['COLUMN_NAME'].lower()
    verbose("%s.%s AUTO_INCREMENT column is %s" % (database_name, read_table_name, unique_key_column_name))

    return unique_key_column_name


def get_table_engine():
    """
    Return the storage engine (lowercase) the given table belongs to.
    """
    engine = None

    query = """
        SELECT ENGINE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA='%s'
            AND TABLE_NAME='%s'
        """ % (database_name, original_table_name)

    row = get_row(query)
    if row:
        engine = row['ENGINE'].lower()
        verbose("Table %s.%s is of engine %s" % (database_name, original_table_name, engine))

    return engine


def validate_no_triggers_exist():
    """
    No 'AFTER' triggers allowed on table, since this utility creates all three AFTER
    triggers (INSERT, UPDATE, DELETE)
    """

    query = """
        SELECT COUNT(*) AS count
        FROM INFORMATION_SCHEMA.TRIGGERS
        WHERE TRIGGER_SCHEMA='%s'
            AND EVENT_OBJECT_TABLE='%s'
            AND ACTION_TIMING='AFTER'
        """ % (database_name, original_table_name)

    row = get_row(query)
    count = int(row['count'])

    return count == 0


def table_exists(check_table_name):
    """
    See if the a given table exists:
    """
    count = 0

    query = """
        SELECT COUNT(*) AS count
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA='%s'
            AND TABLE_NAME='%s'
        """ % (database_name, check_table_name)

    row = get_row(query)
    count = int(row['count'])

    return count


def drop_table(drop_table_name):
    """
    Drop the given table
    """
    if table_exists(drop_table_name):
        query = "DROP TABLE IF EXISTS %s.%s" % (database_name, drop_table_name)
        act_query(query)
        verbose("Table %s.%s was found and dropped" % (database_name, drop_table_name))


def create_ghost_table():
    """
    Create the ghost table in the likes of the original table.
    Later on, it will be altered.
    """

    drop_table(ghost_table_name)

    query = "CREATE TABLE %s.%s LIKE %s.%s" % (database_name, ghost_table_name, database_name, original_table_name)
    act_query(query)
    verbose("Table %s.%s has been created" % (database_name, ghost_table_name))


def alter_ghost_table():
    """
    Perform the ALTER TABLE on the ghost table
    """

    if not options.alter_statement:
        verbose("No ALTER statement provided")
        return
    query = "ALTER TABLE %s.%s %s" % (database_name, ghost_table_name, options.alter_statement)
    act_query(query)
    verbose("Table %s.%s has been altered" % (database_name, ghost_table_name))


def get_table_columns(read_table_name):
    """
    Return the list of column names (lowercase) for the given table
    """
    query = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='%s'
            AND TABLE_NAME='%s'
        """ % (database_name, read_table_name)
    column_names = set([row["COLUMN_NAME"].lower() for row in get_rows(query)])

    return column_names


def get_shared_columns():
    """
    Return the set of columns which are shared between the original table
    and the ghost (altered) table.
    """
    original_columns = get_table_columns(original_table_name)
    ghost_columns = get_table_columns(ghost_table_name)
    shared_columns  = original_columns.intersection(ghost_columns)
    verbose("Shared columns: %s" % ", ".join(shared_columns))

    return shared_columns


def lock_tables_write():
    """
    Lock the original and ghost tables in WRITE mode.
    This can fail due to InnoDB deadlocks, so we keep trying endlessly until it succeeds.
    """
    query = """
        LOCK TABLES %s.%s WRITE, %s.%s WRITE
        """ % (database_name, original_table_name, database_name, ghost_table_name)
    verbose("Attempting to lock tables")
    lock_succeeded = False
    while not lock_succeeded:
        try:
            act_query(query)
            lock_succeeded = True
        except:
            verbose("...")
            time.sleep(0.1)
    print
    verbose("Tables locked WRITE")


def lock_tables_read():
    query = """
       LOCK TABLES %s.%s READ, %s.%s WRITE
         """ % (database_name, original_table_name, database_name, ghost_table_name)
    act_query(query)
    verbose("Tables locked READ, WRITE")


def unlock_tables():
    query = """
        UNLOCK TABLES
        """
    act_query(query)
    verbose("Tables unlocked")


def create_custom_triggers():
    """
    Create the three 'AFTER' triggers on the original table
    """
    query = """
        CREATE TRIGGER %s.%s AFTER DELETE ON %s.%s
        FOR EACH ROW
            DELETE FROM %s.%s WHERE %s = OLD.%s;
        """ % (database_name, after_delete_trigger_name, database_name, original_table_name,
               database_name, ghost_table_name, unique_key_column_name, unique_key_column_name)
    act_query(query)
    verbose("Created AD trigger")

    shared_columns_listing = ", ".join(shared_columns)
    shared_columns_new_listing = ", ".join(["NEW.%s" % column_name for column_name in shared_columns])

    query = """
        CREATE TRIGGER %s.%s AFTER UPDATE ON %s.%s
        FOR EACH ROW
            REPLACE INTO %s.%s (%s) VALUES (%s);
        """ % (database_name, after_update_trigger_name, database_name, original_table_name,
               database_name, ghost_table_name, shared_columns_listing, shared_columns_new_listing)
    act_query(query)
    verbose("Created AU trigger")

    query = """
        CREATE TRIGGER %s.%s AFTER INSERT ON %s.%s
        FOR EACH ROW
            REPLACE INTO %s.%s (%s) VALUES (%s);
        """ % (database_name, after_insert_trigger_name, database_name, original_table_name,
               database_name, ghost_table_name, shared_columns_listing, shared_columns_new_listing)
    act_query(query)
    verbose("Created AI trigger")


def trigger_exists(trigger_name):
    """
    See if the given trigger exists on the original table
    """

    query = """
        SELECT COUNT(*) AS count
        FROM INFORMATION_SCHEMA.TRIGGERS
        WHERE TRIGGER_SCHEMA='%s'
            AND EVENT_OBJECT_TABLE='%s'
            AND TRIGGER_NAME='%s'
        """ % (database_name, original_table_name, trigger_name)

    row = get_row(query)
    count = int(row['count'])

    return count


def drop_custom_trigger(trigger_name):
    if trigger_exists(trigger_name):
        query = """
            DROP TRIGGER IF EXISTS %s.%s
            """ % (database_name, trigger_name)
        act_query(query)
        verbose("Dropped custom trigger %s" % trigger_name)


def drop_custom_triggers():
    """
    Cleanup
    """
    drop_custom_trigger(after_delete_trigger_name)
    drop_custom_trigger(after_update_trigger_name)
    drop_custom_trigger(after_insert_trigger_name)


def get_unique_key_range():
    """
    Return the MIN and MAX values for the AUTO INCREMENT column in the original table
    """
    query = """
        SELECT
          MIN(%s), MAX(%s)
        FROM %s.%s
        INTO @unique_key_min_value, @unique_key_max_value
        """ % (unique_key_column_name, unique_key_column_name,
               database_name, original_table_name)
    act_query(query)

    unique_key_min_value = get_session_variable_value("unique_key_min_value")
    unique_key_max_value = get_session_variable_value("unique_key_max_value")
    verbose("%s (min, max) values: (%s, %s)" % (unique_key_column_name, unique_key_min_value, unique_key_max_value))

    return unique_key_min_value, unique_key_max_value


def set_unique_key_range_end():
    query = """
        SELECT MAX(%s)
        FROM (SELECT %s FROM %s.%s
          WHERE %s BETWEEN @unique_key_range_start AND @unique_key_max_value
          ORDER BY %s LIMIT %d) SEL1
        INTO @unique_key_range_end
        """ % (unique_key_column_name,
               unique_key_column_name, database_name, original_table_name,
               unique_key_column_name,
               unique_key_column_name, options.chunk_size)
    act_query(query)


def set_unique_key_next_range_start():
    """
    Calculate the starting point of the next range
    """
    if unique_key_type == "integer":
        query = "SELECT @unique_key_range_end+1 INTO @unique_key_range_start"
    else:
        query = "SELECT @unique_key_range_end INTO @unique_key_range_start"
    act_query(query)


def is_range_overflow():
    query = """
        SELECT (@unique_key_range_start >= @unique_key_max_value) AND (@unique_key_range_end IS NOT NULL) AS range_overflow
        """
    row = get_row(query)
    range_overflow = row["range_overflow"]
    return range_overflow


def act_data_pass(data_pass_query, description):
    if unique_key_min_value is None:
        return

    query = """
        SELECT @unique_key_min_value, NULL INTO @unique_key_range_start, @unique_key_range_end
        """
    act_query(query)

    while not is_range_overflow():
        set_unique_key_range_end()

        if options.lock_chunks:
            lock_tables_read()

        progress_indicator = "N/A"
        unique_key_range_start = get_session_variable_value("unique_key_range_start")
        unique_key_range_end = get_session_variable_value("unique_key_range_end")
        if unique_key_type == "integer":
            query = """
                SELECT CONVERT(
                    100.0*
                    (@unique_key_range_start-@unique_key_min_value)/
                    (@unique_key_max_value-@unique_key_min_value),
                UNSIGNED) AS progress
                """
            progress = int(get_row(query)["progress"])
            verbose("%s range (%s, %s), progress: %d%%" % (description, unique_key_range_start, unique_key_range_end, progress))
        elif unique_key_type == "temporal":
            query = """
                SELECT CONVERT(
                    100.0*
                    TIMESTAMPDIFF(SECOND, @unique_key_min_value, @unique_key_range_start)/
                    TIMESTAMPDIFF(SECOND, @unique_key_min_value, @unique_key_max_value),
                UNSIGNED) AS progress
                """
            progress = int(get_row(query)["progress"])
            verbose("%s range ('%s', '%s'), progress: %d%%" % (description, unique_key_range_start, unique_key_range_end, progress))
        elif unique_key_type == "text":
            verbose("%s range ('%s', '%s'), progress: N/A" % (description, unique_key_range_start, unique_key_range_end))
        else:
            verbose("%s range (%s, %s), progress: N/A" % (description, unique_key_range_start, unique_key_range_end))

        act_query(data_pass_query)

        if options.lock_chunks:
            unlock_tables()

        set_unique_key_next_range_start()

        if options.sleep_millis > 0:
            sleep_seconds = options.sleep_millis/1000.0
            verbose("Will sleep for %f seconds" % sleep_seconds)
            time.sleep(sleep_seconds)
    verbose("%s range 100%% complete" % description)


def copy_data_pass():
    if unique_key_min_value is None:
        return

    shared_columns_listing = ", ".join(shared_columns)
    engine_flags = ""
    if table_engine == "innodb":
        engine_flags = "LOCK IN SHARE MODE"
    data_pass_query = """
        INSERT IGNORE INTO %s.%s (%s)
            (SELECT %s FROM %s.%s WHERE %s BETWEEN @unique_key_range_start AND @unique_key_range_end
            %s)
        """ % (database_name, ghost_table_name, shared_columns_listing,
            shared_columns_listing, database_name, original_table_name, unique_key_column_name,
            engine_flags)
    act_data_pass(data_pass_query, "Copying")


def delete_data_pass():
    if unique_key_min_value is None:
        return

    shared_columns_listing = ", ".join(shared_columns)
    data_pass_query = """
        DELETE FROM %s.%s
        WHERE %s BETWEEN @unique_key_range_start AND @unique_key_range_end
        AND %s NOT IN
            (SELECT %s FROM %s.%s WHERE %s BETWEEN @unique_key_range_start AND @unique_key_range_end)
        """ % (database_name, ghost_table_name,
            unique_key_column_name,
            unique_key_column_name,
            unique_key_column_name, database_name, original_table_name, unique_key_column_name)

    act_data_pass(data_pass_query, "Deleting")



def rename_tables():
    """
    """

    drop_table(archive_table_name)
    query = """
        RENAME TABLE
            %s.%s TO %s.%s,
            %s.%s TO %s.%s
        """ % (database_name, original_table_name, database_name, archive_table_name,
               database_name, ghost_table_name, database_name, original_table_name, )
    act_query(query)
    verbose("Table %s.%s has been renamed to %s.%s," % (database_name, original_table_name, database_name, archive_table_name))
    verbose("and table %s.%s has been renamed to %s.%s" % (database_name, ghost_table_name, database_name, original_table_name))


def cleanup():
    """
    Remove any data this utility may have created during this runtime or previous runtime.
    """
    if conn:
        drop_table(ghost_table_name)
        drop_table(archive_table_name)
        drop_custom_triggers()
    
    
def exit_with_error(error_message):
    """
    Notify, cleanup and exit.
    """
    print_error("Errors found. Initiating cleanup")
    cleanup()
    print_error(error_message)
    exit(1)
    
    
try:
    try:
        conn = None
        reuse_conn = True
        (options, args) = parse_options()

        if not options.table:
            exit_with_error("No table specified. Specify with -t or --table")
 
        if options.chunk_size <= 0:
            exit_with_error("Chunk size must be nonnegative number. You can leave the default 1000 if unsure")
 
        database_name = None
        original_table_name =  None

        if options.database:
            database_name=options.database

        table_tokens = options.table.split(".")
        original_table_name = table_tokens[-1]
        if len(table_tokens) == 2:
            database_name = table_tokens[0]

        if not database_name:
            exit_with_error("No database specified. Specify with fully qualified table name or with -d or --database")

        conn = open_connection()

        if options.ghost:
            if table_exists(options.ghost):
                exit_with_error("Ghost table: %s.%s already exists." % (database_name, options.ghost))

        if options.ghost:
            ghost_table_name = options.ghost
        else:
            ghost_table_name = "__oak_"+original_table_name
        archive_table_name = "__arc_"+original_table_name

        after_delete_trigger_name = "%s_AD_oak" % original_table_name
        after_update_trigger_name = "%s_AU_oak" % original_table_name
        after_insert_trigger_name = "%s_AI_oak" % original_table_name

        if options.cleanup:
            # All we do now is clean up
            cleanup()
        else:
            table_engine = get_table_engine()
            if not table_engine:
                exit_with_error("Table %s.%s does not exist" % (database_name, original_table_name))

            drop_custom_triggers()
            if not validate_no_triggers_exist():
                exit_with_error("Table must not have any 'AFTER' triggers defined.")

            original_table_unique_key_names = get_possible_unique_key_column_names(original_table_name)
            if not original_table_unique_key_names:
                exit_with_error("Table must have a UNIQUE KEY on a single column")

            create_ghost_table()
            alter_ghost_table()

            ghost_table_unique_key_names = get_possible_unique_key_column_names(ghost_table_name)
            if not original_table_unique_key_names:
                drop_table(ghost_table_name)
                exit_with_error("Aletered table must have a UNIQUE KEY on a single column")

            shared_unique_key_column_names = original_table_unique_key_names.intersection(ghost_table_unique_key_names)

            if not shared_unique_key_column_names:
                drop_table(ghost_table_name)
                exit_with_error("Altered table must retain at least one unique key")

            unique_key_column_name, unique_key_type = get_shared_unique_key_column(shared_unique_key_column_names)

            shared_columns = get_shared_columns()

            create_custom_triggers()
            lock_tables_write()
            unique_key_min_value, unique_key_max_value = get_unique_key_range()
            unlock_tables()

            copy_data_pass()
            delete_data_pass()

            if options.ghost:
                verbose("Ghost table creation completed. Note that triggers on %s.%s were not removed" % (database_name, original_table_name))
            else:
                rename_tables()
                drop_table(archive_table_name)
                verbose("ALTER TABLE completed")
    except Exception, err:
        exit_with_error(err)
finally:
    if conn:
        conn.close()
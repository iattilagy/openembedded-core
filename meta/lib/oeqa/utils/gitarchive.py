#
# Helper functions for committing data to git and pushing upstream
#
# Copyright (c) 2017, Intel Corporation.
# Copyright (c) 2019, Linux Foundation
#
# This program is free software; you can redistribute it and/or modify it
# under the terms and conditions of the GNU General Public License,
# version 2, as published by the Free Software Foundation.
#
# This program is distributed in the hope it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#

import os
import re
import sys
from oeqa.utils.git import GitRepo, GitError

class ArchiveError(Exception):
    """Internal error handling of this script"""

def format_str(string, fields):
    """Format string using the given fields (dict)"""
    try:
        return string.format(**fields)
    except KeyError as err:
        raise ArchiveError("Unable to expand string '{}': unknown field {} "
                           "(valid fields are: {})".format(
                               string, err, ', '.join(sorted(fields.keys()))))


def init_git_repo(path, no_create, bare, log):
    """Initialize local Git repository"""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        raise ArchiveError("Invalid Git repo at {}: path exists but is not a "
                           "directory".format(path))
    if not os.path.isdir(path) or not os.listdir(path):
        if no_create:
            raise ArchiveError("No git repo at {}, refusing to create "
                               "one".format(path))
        if not os.path.isdir(path):
            try:
                os.mkdir(path)
            except (FileNotFoundError, PermissionError) as err:
                raise ArchiveError("Failed to mkdir {}: {}".format(path, err))
        if not os.listdir(path):
            log.info("Initializing a new Git repo at %s", path)
            repo = GitRepo.init(path, bare)
    try:
        repo = GitRepo(path, is_topdir=True)
    except GitError:
        raise ArchiveError("Non-empty directory that is not a Git repository "
                           "at {}\nPlease specify an existing Git repository, "
                           "an empty directory or a non-existing directory "
                           "path.".format(path))
    return repo


def git_commit_data(repo, data_dir, branch, message, exclude, notes, log):
    """Commit data into a Git repository"""
    log.info("Committing data into to branch %s", branch)
    tmp_index = os.path.join(repo.git_dir, 'index.oe-git-archive')
    try:
        # Create new tree object from the data
        env_update = {'GIT_INDEX_FILE': tmp_index,
                      'GIT_WORK_TREE': os.path.abspath(data_dir)}
        repo.run_cmd('add .', env_update)

        # Remove files that are excluded
        if exclude:
            repo.run_cmd(['rm', '--cached'] + [f for f in exclude], env_update)

        tree = repo.run_cmd('write-tree', env_update)

        # Create new commit object from the tree
        parent = repo.rev_parse(branch)
        git_cmd = ['commit-tree', tree, '-m', message]
        if parent:
            git_cmd += ['-p', parent]
        commit = repo.run_cmd(git_cmd, env_update)

        # Create git notes
        for ref, filename in notes:
            ref = ref.format(branch_name=branch)
            repo.run_cmd(['notes', '--ref', ref, 'add',
                          '-F', os.path.abspath(filename), commit])

        # Update branch head
        git_cmd = ['update-ref', 'refs/heads/' + branch, commit]
        if parent:
            git_cmd.append(parent)
        repo.run_cmd(git_cmd)

        # Update current HEAD, if we're on branch 'branch'
        if not repo.bare and repo.get_current_branch() == branch:
            log.info("Updating %s HEAD to latest commit", repo.top_dir)
            repo.run_cmd('reset --hard')

        return commit
    finally:
        if os.path.exists(tmp_index):
            os.unlink(tmp_index)


def expand_tag_strings(repo, name_pattern, msg_subj_pattern, msg_body_pattern,
                       keywords):
    """Generate tag name and message, with support for running id number"""
    keyws = keywords.copy()
    # Tag number is handled specially: if not defined, we autoincrement it
    if 'tag_number' not in keyws:
        # Fill in all other fields than 'tag_number'
        keyws['tag_number'] = '{tag_number}'
        tag_re = format_str(name_pattern, keyws)
        # Replace parentheses for proper regex matching
        tag_re = tag_re.replace('(', '\(').replace(')', '\)') + '$'
        # Inject regex group pattern for 'tag_number'
        tag_re = tag_re.format(tag_number='(?P<tag_number>[0-9]{1,5})')

        keyws['tag_number'] = 0
        for existing_tag in repo.run_cmd('tag').splitlines():
            match = re.match(tag_re, existing_tag)

            if match and int(match.group('tag_number')) >= keyws['tag_number']:
                keyws['tag_number'] = int(match.group('tag_number')) + 1

    tag_name = format_str(name_pattern, keyws)
    msg_subj= format_str(msg_subj_pattern.strip(), keyws)
    msg_body = format_str(msg_body_pattern, keyws)
    return tag_name, msg_subj + '\n\n' + msg_body

def gitarchive(data_dir, git_dir, no_create, bare, commit_msg_subject, commit_msg_body, branch_name, no_tag, tagname, tag_msg_subject, tag_msg_body, exclude, notes, push, keywords, log):

    if not os.path.isdir(data_dir):
        raise ArchiveError("Not a directory: {}".format(data_dir))

    data_repo = init_git_repo(git_dir, no_create, bare, log)

    # Expand strings early in order to avoid getting into inconsistent
    # state (e.g. no tag even if data was committed)
    commit_msg = format_str(commit_msg_subject.strip(), keywords)
    commit_msg += '\n\n' + format_str(commit_msg_body, keywords)
    branch_name = format_str(branch_name, keywords)
    tag_name = None
    if not no_tag and tagname:
        tag_name, tag_msg = expand_tag_strings(data_repo, tagname,
                                               tag_msg_subject,
                                               tag_msg_body, keywords)

    # Commit data
    commit = git_commit_data(data_repo, data_dir, branch_name,
                             commit_msg, exclude, notes, log)

    # Create tag
    if tag_name:
        log.info("Creating tag %s", tag_name)
        data_repo.run_cmd(['tag', '-a', '-m', tag_msg, tag_name, commit])

    # Push data to remote
    if push:
        cmd = ['push', '--tags']
        # If no remote is given we push with the default settings from
        # gitconfig
        if push is not True:
            notes_refs = ['refs/notes/' + ref.format(branch_name=branch_name)
                           for ref, _ in notes]
            cmd.extend([push, branch_name] + notes_refs)
        log.info("Pushing data to remote")
        data_repo.run_cmd(cmd)

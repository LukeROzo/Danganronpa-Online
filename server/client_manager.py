# tsuserver3, an Attorney Online server
#
# Copyright (C) 2016 argoneus <argoneuscze@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import time
import re
import random

from server import fantacrypt
from server import logger
from server.exceptions import ClientError, AreaError
from server.constants import TargetType

class ClientManager:
    class Client:
        def __init__(self, server, transport, user_id, ipid):
            self.transport = transport
            self.hdid = ''
            self.pm_mute = False
            self.id = user_id
            self.char_id = None
            self.area = server.area_manager.default_area()
            self.server = server
            self.name = ''
            self.fake_name = ''
            self.is_mod = False
            self.is_gm = False
            self.is_dj = True
            self.pos = ''
            self.is_cm = False
            self.evi_list = []
            self.disemvowel = False
            self.remove_h = False
            self.disemconsonant = False
            self.gimp = False
            self.muted_global = False
            self.muted_adverts = False
            self.is_muted = False
            self.is_ooc_muted = False
            self.pm_mute = False
            self.mod_call_time = 0
            self.in_rp = False
            self.ipid = ipid
            self.is_visible = True
            self.multi_ic = None
            self.showname = ''
            self.following = None
            self.followedby = None
            self.music_list = None
            self.autopass = False
            self.showname_history = list()
            self.is_transient = False
            self.handicap_backup = None # Use if custom handicap is overwritten with a server one
            self.is_movement_handicapped = False
            self.show_shownames = True
            self.is_bleeding = False

            #music flood-guard stuff
            self.mus_counter = 0
            self.mute_time = 0
            self.mflood_interval = self.server.config['music_change_floodguard']['interval_length']
            self.mflood_times = self.server.config['music_change_floodguard']['times_per_interval']
            self.mflood_mutelength = self.server.config['music_change_floodguard']['mute_length']
            self.mus_change_time = [x * self.mflood_interval for x in range(self.mflood_times)]

        def send_raw_message(self, msg):
            self.transport.write(msg.encode('utf-8'))

        def send_command(self, command, *args):
            if args:
                if command == 'MS':
                    for evi_num in range(len(self.evi_list)):
                        if self.evi_list[evi_num] == args[11]:
                            lst = list(args)
                            lst[11] = evi_num
                            args = tuple(lst)
                            break
                self.send_raw_message('{}#{}#%'.format(command, '#'.join([str(x) for x in args])))
            else:
                self.send_raw_message('{}#%'.format(command))

        def send_host_message(self, msg):
            self.send_command('CT', self.server.config['hostname'], msg)

        def send_host_others(self, msg, is_staff=None, in_area=None, pred=None):
            if is_staff is True:
                cond1 = lambda c: c.is_staff()
            elif is_staff is False:
                cond1 = lambda c: not c.is_staff()
            elif is_staff is None:
                cond1 = lambda c: True
            else:
                raise KeyError('Invalid argument for send_host_others is_staff: {}'.format(is_staff))

            if in_area is True:
                cond2 = lambda c: c.area == self.area
            elif type(in_area) is type(self.area): # Lazy way of checking if in_area is an area obj
                cond2 = lambda c: c.area == in_area
            elif in_area is None:
                cond2 = lambda c: True
            else:
                raise KeyError('Invalid argument for send_host_others in_area: {}'.format(in_area))

            if pred is None:
                pred = lambda c: True

            cond = lambda c: c != self and cond1(c) and cond2(c) and pred(c)

            self.server.send_all_cmd_pred('CT', self.server.config['hostname'], msg, pred=cond)

        def send_motd(self):
            self.send_host_message('=== MOTD ===\r\n{}\r\n============='
                                   .format(self.server.config['motd']))

        def is_valid_name(self, name):
            name_ws = name.replace(' ', '')
            if not name_ws or name_ws.isdigit():
                return False
            #for client in self.server.client_manager.clients:
                #print(client.name == name)
                #if client.name == name:
                    #return False
            return True

        def disconnect(self):
            self.transport.close()

        def is_staff(self):
            """
            Returns True if logged in as 'any' staff role.
            """
            return self.is_mod or self.is_cm or self.is_gm

        def change_character(self, char_id, force=False, target_area=None):
            # Added target_area parameter because when switching areas, the change character code
            # is run before the character's area actually changes, so it would look for the wrong
            # area if I just did self.area
            if target_area is None:
                target_area = self.area

            if not self.server.is_valid_char_id(char_id):
                raise ClientError('Invalid Character ID.')
            if not target_area.is_char_available(char_id, allow_restricted=self.is_staff()):
                if force:
                    for client in self.area.clients:
                        if client.char_id == char_id:
                            client.char_select()
                else:
                    raise ClientError('Character {} not available.'
                                      .format(self.get_char_name(char_id)))

            if self.char_id < 0 and char_id >= 0: # No longer spectator?
                # Now bound by AFK rules
                self.server.create_task(self, ['as_afk_kick', self.area.afk_delay,
                                               self.area.afk_sendto])

            old_char = self.get_char_name()
            self.char_id = char_id
            self.pos = ''
            self.send_command('PV', self.id, 'CID', self.char_id)
            logger.log_server('[{}]Changed character from {} to {}.'
                              .format(self.area.id, old_char, self.get_char_name()), self)

        def change_music_cd(self):
            if self.is_mod or self.is_cm:
                return 0
            if self.mute_time:
                if time.time() - self.mute_time < self.mflood_mutelength:
                    return self.mflood_mutelength - (time.time() - self.mute_time)
                else:
                    self.mute_time = 0

            index = (self.mus_counter - self.mflood_times + 1) % self.mflood_times
            if time.time() - self.mus_change_time[index] < self.mflood_interval:
                self.mute_time = time.time()
                return self.server.config['music_change_floodguard']['mute_length']

            self.mus_counter = (self.mus_counter + 1) % self.mflood_interval
            self.mus_change_time[self.mus_counter] = time.time()
            return 0

        def reload_character(self):
            try:
                self.change_character(self.char_id, True)
            except ClientError:
                raise

        def reload_music_list(self, new_music_file=None):
            """
            Rebuild the music list so that it only contains the target area's
            reachable areas+music. Useful when moving areas/logging in or out.
            """
            if new_music_file:
                new_music_list = self.server.load_music(music_list_file=new_music_file,
                                                        server_music_list=False)
                self.music_list = new_music_list
                self.server.build_music_list_ao2(from_area=self.area, c=self,
                                                 music_list=new_music_list)
            else:
                self.server.build_music_list_ao2(from_area=self.area, c=self)
            # KEEP THE ASTERISK, unless you want a very weird single area comprised
            # of all areas back to back forming a black hole area of doom and despair
            # that crashes all clients that dare attempt join this area.
            self.send_command('FM', *self.server.music_list_ao2)

        def change_area(self, area, override_all=False, override_passages=False,
                        override_effects=False, ignore_bleeding=False, ignore_followers=False):
            """
            PARAMETERS:
            *override_passages: ignore passages existing from the source area to the target area
            *override_effects: ignore current effects, such as movement handicaps
            *ignore_bleeding: not add blood to the area if the character is moving,
             such as from /area_kick or AFK kicks
            *ignore_followers: avoid sending the follow command to followers (e.g. using /follow)
            *override_all: perform the area change regardless of anything (only useful for complete area reload)
             In particular, override_all being False performs all the checks and announces the area change in OOC
            """
            if not override_all:
                # Check if player has waited a non-zero movement delay
                if not self.is_staff() and self.is_movement_handicapped and not override_effects:
                    start, length, name, _ = self.server.get_task_args(self, ['as_handicap'])
                    _, remain_text = self.server.timer_remaining(start, length)
                    raise ClientError("You are still under the effects of movement handicap '{}'. Please wait {} before changing areas.".format(name, remain_text))

                # Check if trying to move to a lobby/private area while sneaking
                if area.lobby_area and not self.is_visible and not self.is_mod and not self.is_cm:
                    raise ClientError('Lobby areas do not let non-authorized users remain sneaking. Please change the music, speak IC or ask a staff member to reveal you.')
                elif area.private_area and not self.is_visible:
                    raise ClientError('Private areas do not let sneaked users in. Please change the music, speak IC or ask a staff member to reveal you.')

                # Check if area has some sort of lock
                if self.area == area:
                    raise ClientError('User is already in target area.')
                if area.is_locked and not self.is_mod and not self.is_gm and not self.ipid in area.invite_list:
                    raise ClientError('That area is locked.')
                if area.is_gmlocked and not self.is_mod and not self.is_gm and not self.ipid in area.invite_list:
                    raise ClientError('That area is gm-locked.')
                if area.is_modlocked and not self.is_mod and not self.ipid in area.invite_list:
                    raise ClientError('That area is mod-locked.')

                # Check if trying to reach an unreachable area
                if not (area.name in self.area.reachable_areas or '<ALL>' in self.area.reachable_areas or \
                self.is_transient or self.is_staff() or override_passages):
                    info = 'Selected area cannot be reached from the current one without authorization. Try one of the following instead: '
                    if self.area.reachable_areas == {self.area.name}:
                        info += '\r\n*No areas available.'
                    else:
                        try:
                            sorted_areas = sorted(self.area.reachable_areas, key=lambda area_name: self.server.area_manager.get_area_by_name(area_name).id)
                            for reachable_area in sorted_areas:
                                if reachable_area != self.area.name:
                                    info += '\r\n*({}) {}'.format(self.server.area_manager.get_area_by_name(reachable_area).id, reachable_area)
                        except AreaError: #When would you ever execute this piece of code is beyond me, but meh
                            info += '\r\n<ALL>'
                    raise ClientError(info)

                # Check if current character is taken in the new area
                old_char = self.get_char_name()
                if not area.is_char_available(self.char_id, allow_restricted=self.is_staff()):
                    try:
                        new_char_id = area.get_rand_avail_char_id(allow_restricted=self.is_staff())
                    except AreaError:
                        raise ClientError('No available characters in that area.')

                    self.change_character(new_char_id, target_area=area)
                    if old_char in area.restricted_chars:
                        self.send_host_message('Your character was restricted in your new area, switched to {}.'.format(self.get_char_name()))
                    else:
                        self.send_host_message('Your character was taken in your new area, switched to {}.'.format(self.get_char_name()))

                # Code after this line assumes that the area change will be successful
                # (but has not yet been performed)

                old_area = self.area
                self.send_host_message('Changed area to {}.[{}]'.format(area.name, area.status))

                # Check if someone in the new area has the same showname
                try:
                    self.change_showname(self.showname, target_area=area) # Verify that showname is still valid
                except ValueError:
                    self.send_host_message("Your showname {} was already used in this area. Resetting it to none.".format(self.showname))
                    self.change_showname('', target_area=area)
                    logger.log_server('{} had their showname removed due it being used in the new area.'.format(self.ipid), self)

                # Check if the lights were turned off, and if so, let the client know
                if not area.lights:
                    self.send_host_message('You enter a pitch dark room.')

                # If autopassing, send OOC messages, provided the lights are on
                if self.autopass and not self.char_id < 0:
                    self.server.send_all_cmd_pred('CT', '{}'.format(self.server.config['hostname']),
                                                  '{} has left to the {}.'.format(old_char, area.name),
                                                  pred=lambda c: (c != self and c.area == old_area
                                                  and (c.is_staff() or (old_area.lights and self.is_visible))))
                    self.server.send_all_cmd_pred('CT', '{}'.format(self.server.config['hostname']),
                                                  '{} has entered from the {}.'.format(self.get_char_name(), old_area.name),
                                                  pred=lambda c: (c != self and c.area == area
                                                  and (c.is_staff() or (area.lights and self.is_visible))))

                # If former or new area's lights are turned off, send special messages to non-staff
                # announcing your presence
                if not old_area.lights and not self.char_id < 0 and self.is_visible:
                    for c in [x for x in old_area.clients if not x.is_staff() and x != self]:
                        c.send_host_message('You hear footsteps going out of the room.')

                if not area.lights and not self.char_id < 0 and self.is_visible:
                    for c in [x for x in area.clients if not x.is_staff() and x != self]:
                        c.send_host_message('You hear footsteps coming into the room.')

                # If bleeding, send reminder, and notify everyone in the new area if not sneaking
                # (otherwise, just send vague message).
                if self.is_bleeding:
                    old_area.bleeds_to.add(old_area.name)
                    area.bleeds_to.add(area.name)
                if not ignore_bleeding and self.is_bleeding:
                    # As these are sets, repetitions are automatically filtered out
                    old_area.bleeds_to.add(area.name)
                    area.bleeds_to.add(old_area.name)
                    self.send_host_message('You are bleeding.')

                    # Send notification to people in new area
                    area_had_bleeding = (len([c for c in area.clients if c.is_bleeding]) > 0)
                    if self.is_visible and area.lights:
                        normal_mes = 'You see {} arrive and bleeding.'.format(self.get_char_name())
                        staff_mes = normal_mes
                    elif not self.is_visible and area.lights:
                        s = {True: 'more', False: 'faint'}
                        normal_mes = ('You start hearing {} drops of blood.'
                                      .format(s[area_had_bleeding]))
                        staff_mes = ('{} arrived to the area while bleeding and sneaking.'
                                     .format(self.get_char_name()))
                    elif self.is_visible and not area.lights:
                        s = {True: 'more ', False: ''}
                        normal_mes = ('You start hearing and smelling {}drips of blood.'
                                      .format(s[area_had_bleeding]))
                        staff_mes = ('{} arrived to the darkened area while bleeding.'
                                     .format(self.get_char_name()))
                    elif not self.is_visible and not area.lights:
                        s = {True: 'more ', False: ''}
                        normal_mes = ('You start hearing and smelling {}drips of blood.'
                                      .format(self.get_char_name()))
                        staff_mes = ('{} arrived to the darkened area while bleeding and sneaking.'
                                     .format(self.get_char_name()))

                    self.send_host_others(normal_mes, is_staff=False, in_area=True)
                    self.send_host_others(staff_mes, is_staff=True, in_area=True)

                # If bleeding and either you were sneaking or your former area had its lights turned
                # off, notify everyone in the new area to the less intense sounds and smells of
                # blood. Do nothing if lights on and not sneaking.
                if not ignore_bleeding and self.is_bleeding:
                    area_sole_bleeding = (len([c for c in old_area.clients if c.is_bleeding]) == 1)
                    if self.is_visible and old_area.lights:
                        normal_mes = ''
                        staff_mes = ''
                    elif not self.is_visible and old_area.lights:
                        s = {True: 'stop hearing', False: 'start hearing less'}
                        normal_mes = 'You {} drops of blood.'.format(s[area_sole_bleeding])
                        staff_mes = ('{} left the area while bleeding and sneaking.'
                                     .format(self.get_char_name()))
                    elif self.is_visible and not old_area.lights:
                        s = {True: 'stop hearing and smelling',
                             False: 'start hearing and smelling less'}
                        normal_mes = 'You {} drops of blood.'.format(s[area_sole_bleeding])
                        staff_mes = ('{} left the darkened area while bleeding.'
                                     .format(self.get_char_name()))
                    elif not self.is_visible and not old_area.lights:
                        s = {True: 'stop hearing and smelling',
                             False: 'start hearing and smelling less'}
                        normal_mes = 'You {} drops of blood.'.format(s[area_sole_bleeding])
                        staff_mes = ('{} left the darkened area while bleeding and sneaking.'
                                     .format(self.get_char_name()))

                    if normal_mes and staff_mes:
                        self.send_host_others(normal_mes, is_staff=False, in_area=old_area)
                        self.send_host_others(staff_mes, is_staff=True, in_area=old_area)

                # If someone else is bleeding in the new area, notify the person moving
                # Special consideration is given if that someone else is sneaking or the area's
                # lights are turned off, or the client is a staff member
                bleeding_visible = [c for c in area.clients if c.is_visible and c.is_bleeding]
                bleeding_sneaking = [c for c in area.clients if not c.is_visible and c.is_bleeding]
                info = ''
                sneak_info = ''

                # If lights are out and someone is bleeding, send generic drops of blood message
                # to non-staff members
                if not area.lights and not self.is_staff() and len(bleeding_visible + bleeding_sneaking) > 0:
                    info = 'You hear faint drops of blood'
                # Otherwise, send who is bleeding if not sneaking and alert to the sound of drops of
                # blood if someone is bleeding while sneaking. Staff members get notified of the
                # names of the people who are bleeding and sneaking.
                else:
                    if len(bleeding_visible) == 1:
                        info = 'You see {} is bleeding'.format(bleeding_visible[0].get_char_name())
                    elif len(bleeding_visible) > 1:
                        info = 'You see {}'.format(bleeding_visible[0].get_char_name())
                        for i in range(1, len(bleeding_visible)-1):
                            info += ', {}'.format(bleeding_visible[i].get_char_name())
                        info += ' and {} are bleeding'.format(bleeding_visible[-1].get_char_name())

                    if len(bleeding_sneaking) > 0:
                        if not self.is_staff():
                            sneak_info = 'You hear faint drops of blood'
                        elif len(bleeding_sneaking) == 1:
                            sneak_info = 'You see {} is bleeding while sneaking'.format(bleeding_sneaking[0].get_char_name())
                        else:
                            sneak_info = 'You see {}'.format(bleeding_sneaking[0].get_char_name())
                            for i in range(1, len(bleeding_sneaking)-1):
                                sneak_info += ', {}'.format(bleeding_sneaking[i].get_char_name())
                            sneak_info += (' and {} are bleeding while sneaking'
                                           .format(bleeding_sneaking[-1].get_char_name()))

                if info != '':
                    if sneak_info != '':
                        sneak_info = sneak_info[:1].lower() + sneak_info[1:]
                        info = '{}, and {}'.format(info, sneak_info)
                else:
                    info = sneak_info

                if info != '':
                    self.send_host_message(info + '.')

                # If there is blood in the area, send notification
                if area.bleeds_to == set([area.name]):
                    self.send_host_message('You spot some blood in the area.')
                elif len(area.bleeds_to) > 1:
                    bleed_to_areas = list(area.bleeds_to - set([area.name]))
                    random.shuffle(bleed_to_areas) # Lose potential order bias, yes, even with what originally was a set
                    info = 'You spot a blood trail leading to the {}'.format(bleed_to_areas[0])
                    if len(bleed_to_areas) > 1:
                        for i in range(1, len(bleed_to_areas)-1):
                            info += ', the {}'.format(bleed_to_areas[i])
                        info += ' and the {}.'.format(bleed_to_areas[-1])
                    else:
                        info += '.'
                    self.send_host_message(info)

                logger.log_server('[{}]Changed area from {} ({}) to {} ({}).'
                                  .format(self.get_char_name(), old_area.name, old_area.id, area.name, area.id), self)
                #logger.log_rp(
                #    '[{}]Changed area from {} ({}) to {} ({}).'.format(self.get_char_name(), old_area.name, old_area.id,
                #                                                       self.area.name, self.area.id), self)

            self.area.remove_client(self)
            self.area = area
            area.new_client(self)

            self.send_command('HP', 1, self.area.hp_def)
            self.send_command('HP', 2, self.area.hp_pro)
            self.send_command('BN', self.area.background)
            self.send_command('LE', *self.area.get_evidence_list(self))

            if self.followedby is not None and not ignore_followers:
                self.followedby.follow_area(area)

            self.reload_music_list() # Update music list to include new area's reachable areas
            self.server.create_task(self, ['as_afk_kick', area.afk_delay, area.afk_sendto])
            # Try and restart handicap if needed
            try:
                _, length, name, announce_if_over = self.server.get_task_args(self, ['as_handicap'])
            except (ValueError, KeyError):
                pass
            else:
                self.server.create_task(self, ['as_handicap', time.time(), length, name, announce_if_over])

        def change_showname(self, showname, target_area=None, forced=True):
            # forced=True means that someone else other than the user themselves requested the showname change.
            # Should only be false when using /showname.
            if target_area is None:
                target_area = self.area

            # Check length
            if len(showname) > self.server.config['showname_max_length']:
                raise ClientError("Given showname {} exceeds the server's character limit of {}.".format(showname, self.server.config['showname_max_length']))

            # Check if non-empty showname is already used within area
            if showname != '':
                for c in target_area.clients:
                    if c.showname == showname and c != self:
                        raise ValueError("Given showname {} is already in use in this area.".format(showname))
                        # This ValueError must be recaught, otherwise the client will crash.

            if self.showname != showname:
                status = {True: 'Was', False: 'Self'}

                if showname != '':
                    self.showname_history.append("{} | {} set to {}".format(time.asctime(time.localtime(time.time())), status[forced], showname))
                else:
                    self.showname_history.append("{} | {} cleared".format(time.asctime(time.localtime(time.time())), status[forced]))
            self.showname = showname

        def change_visibility(self, new_status):
            if new_status: # Changed to visible (e.g. through /reveal)
                self.send_host_message("You are no longer sneaking.")
                self.is_visible = True

                # Player should also no longer be under the effect of the server's sneaked handicap.
                # Thus, we check if there existed a previous movement handicap that had a shorter delay
                # than the server's sneaked handicap and restore it (as by default the server will take the
                # largest handicap when dealing with the automatic sneak handicap)
                try:
                    _, _, name, _ = self.server.get_task_args(self, ['as_handicap'])
                except KeyError:
                    pass
                else:
                    if name == "Sneaking":
                        if self.server.config['sneak_handicap'] > 0 and self.handicap_backup:
                            # Only way for a handicap backup to exist and to be in this situation is
                            # for the player to had a custom handicap whose length was shorter than the server's
                            # sneak handicap, then was set to be sneaking, then was revealed.
                            # From this, we can recover the old handicap backup
                            _, old_length, old_name, old_announce_if_over = self.handicap_backup[1]

                            self.send_host_others('{} was automatically imposed their former movement handicap "{}" of length {} seconds after being revealed in area {} ({}).'
                                                  .format(self.get_char_name(), old_name, old_length, self.area.name, self.area.id),
                                                  is_staff=True)
                            self.send_host_message('You were automatically imposed your former movement handicap "{}" of length {} seconds when changing areas.'
                                                   .format(old_name, old_length))
                            self.server.create_task(self, ['as_handicap', time.time(), old_length, old_name, old_announce_if_over])
                        else:
                            self.server.remove_task(self, ['as_handicap'])

                logger.log_server('{} is no longer sneaking.'.format(self.ipid), self)
            else: # Changed to invisible (e.g. through /sneak)
                self.send_host_message("You are now sneaking.")
                self.is_visible = False

                # Check to see if should impose the server's sneak handicap on the player
                # This should only happen if two conditions are satisfied:
                # 1. There is a positive sneak handicap and,
                # 2. The player has no movement handicap or, if they do, it is shorter than the sneak handicap
                if self.server.config['sneak_handicap'] > 0:
                    try:
                        _, length, _, _ = self.server.get_task_args(self, ['as_handicap'])
                        if length < self.server.config['sneak_handicap']:
                            self.send_host_others('{} was automatically imposed the longer movement handicap "Sneaking" of length {} seconds in area {} ({}).'
                                                  .format(self.get_char_name(), self.server.config['sneak_handicap'], self.area.name, self.area.id),
                                                  is_staff=True)
                            raise KeyError # Lazy way to get there, but it works
                    except KeyError:
                        self.send_host_message('You were automatically imposed a movement handicap "Sneaking" of length {} seconds when changing areas.'.format(self.server.config['sneak_handicap']))
                        self.server.create_task(self, ['as_handicap', time.time(), self.server.config['sneak_handicap'], "Sneaking", True])

                logger.log_server('{} is now sneaking.'.format(self.ipid), self)

        def follow_user(self, target):
            if target == self:
                raise ClientError('You cannot follow yourself.')
            if target == self.following:
                raise ClientError('You are already following that player.')

            if target.followedby: # Only one person can follow someone else
                target.followedby.send_host_message('{} started following your target, so you are no longer following them.'.format(self.name))
                target.followedby.unfollow_user()

            self.send_host_message('Began following client {} at {}'.format(target.id, time.asctime(time.localtime(time.time()))))
            self.following = target
            target.followedby = self

            if self.area != target.area:
                self.follow_area(target.area, just_moved=False)

        def unfollow_user(self):
            if not self.following:
                raise ClientError('You are not following anyone.')

            self.send_host_message("Stopped following client {} at {}.".format(self.following.id, time.asctime(time.localtime(time.time()))))
            self.following.followedby = None
            self.following = None

        def follow_area(self, area, just_moved=True):
            # just_moved if True assumes the case where the followed user just moved
            # It being false is the case where, when the following started, the followed user was in another area, and thus the followee is moved automtically
            if just_moved:
                self.send_host_message('Followed user moved to {} at {}'.format(area.name, time.asctime(time.localtime(time.time()))))
            else:
                self.send_host_message('Followed user was at {}'.format(area.name))

            try:
                self.change_area(area, ignore_followers=True)
            except ClientError as error:
                self.send_host_message('Unable to follow to {}: {}'.format(area.name, error))

        def send_area_list(self):
            msg = '=== Areas ==='
            lock = {True: '[LOCKED]', False: ''}
            for i, area in enumerate(self.server.area_manager.areas):
                owner = 'FREE'
                if area.owned:
                    for client in [x for x in area.clients if x.is_cm]:
                        owner = 'MASTER: {}'.format(client.get_char_name())
                        break
                locked = area.is_gmlocked or area.is_modlocked or area.is_locked

                if self.is_staff():
                    num_clients = len([c for c in area.clients if c.char_id is not None])
                else:
                    num_clients = len([c for c in area.clients if c.is_visible and c.char_id is not None])

                msg += '\r\nArea {}: {} (users: {}) {}'.format(i, area.name, num_clients, lock[locked])
                if self.area == area:
                    msg += ' [*]'
            self.send_host_message(msg)

        def send_limited_area_list(self):
            msg = '=== Areas ==='
            for i, area in enumerate(self.server.area_manager.areas):
                msg += '\r\nArea {}: {}'.format(i, area.name)
                if self.area == area:
                    msg += ' [*]'
            self.send_host_message(msg)

        def get_area_info(self, area_id, mods, include_shownames=False):
            info = ''
            try:
                area = self.server.area_manager.get_area_by_id(area_id)
            except AreaError:
                raise

            info += '= Area {}: {} =='.format(area.id, area.name)
            sorted_clients = []
            for c in area.clients:
                # Conditions to print out a client in /getarea(s)
                # * Client is not in the server selection screen and,
                # * Any of the four
                # 1. Client is yourself.
                # 2. You are a staff member.
                # 3. Client is visible.
                # 4. Client is a mod when requiring only mods be printed.
                if c.char_id is not None:
                    if c == self or self.is_staff() or c.is_visible or (mods and c.is_mod):
                        sorted_clients.append(c)
            sorted_clients = sorted(sorted_clients, key=lambda x: x.get_char_name())

            for c in sorted_clients:
                info += '\r\n[{}] {}'.format(c.id, c.get_char_name())
                if include_shownames and c.showname != '':
                    info += ' ({})'.format(c.showname)
                if not c.is_visible:
                    info += ' (S)'
                if self.is_mod:
                    info += ' ({})'.format(c.ipid)
            return info

        def send_area_info(self, current_area, area_id, mods, include_shownames=False):
            #If area_id is -1, then return all areas.
            #If mods is True, then return only mods
            #If include_shownames is True, then include non-empty custom shownames.

            # Verify that it should send the area info first
            if not self.is_staff():
                if (area_id == -1 and not self.area.rp_getareas_allowed) or \
                   (area_id != -1 and not self.area.rp_getarea_allowed):
                       raise ClientError('This command has been restricted to authorized users only in this area while in RP mode.')
                if not self.area.lights:
                    raise ClientError('The lights are off. You cannot see anything.')

            # All code from here on assumes the area info will be sent successfully
            info = ''
            if area_id == -1:
                # all areas info
                info = '== Area List =='
                unrestricted_access_area = '<ALL>' in current_area.reachable_areas
                for i in range(len(self.server.area_manager.areas)):
                    # Get area i details...
                    # If staff and there are clients in the area OR
                    # If not staff, there are visible clients in the area, and the area is reachable from the current one
                    not_staff_check = len([c for c in self.server.area_manager.areas[i].clients if c.is_visible or c == self]) > 0 and \
                                      (unrestricted_access_area or self.server.area_manager.areas[i].name in current_area.reachable_areas or self.is_transient)

                    if (self.is_staff() and len(self.server.area_manager.areas[i].clients) > 0) or \
                    (not self.is_staff() and not_staff_check):
                        info += '\r\n{}'.format(self.get_area_info(i, mods, include_shownames=include_shownames))
            else:
                try:
                    info = self.get_area_info(area_id, mods, include_shownames=include_shownames)
                except AreaError:
                    raise
            self.send_host_message(info)

        def send_area_hdid(self, area_id):
            try:
                info = self.get_area_hdid(area_id)
            except AreaError:
                raise
            self.send_host_message(info)

        def get_area_hdid(self, area_id):
            raise NotImplementedError

        def send_all_area_hdid(self):
            info = '== HDID List =='
            for i in range(len(self.server.area_manager.areas)):
                if len(self.server.area_manager.areas[i].clients) > 0:
                    info += '\r\n{}'.format(self.get_area_hdid(i))
            self.send_host_message(info)

        def send_all_area_ip(self):
            info = '== IP List =='
            for i in range(len(self.server.area_manager.areas)):
                if len(self.server.area_manager.areas[i].clients) > 0:
                    info += '\r\n{}'.format(self.get_area_ip(i))
            self.send_host_message(info)

        def get_area_ip(self, ip):
            raise NotImplementedError

        def send_done(self):
            avail_char_ids = set(range(len(self.server.char_list))) - self.area.get_chars_unusable(allow_restricted=self.is_staff())
            # Readd sneaked players if needed so that they don't appear as taken
            # Their characters will not be able to be reused, but at least that's one less clue about their presence.
            if not self.is_staff():
                avail_char_ids |= set([c.char_id for c in self.area.clients if not c.is_visible])

            char_list = [-1] * len(self.server.char_list)
            for x in avail_char_ids:
                char_list[x] = 0
            self.send_command('CharsCheck', *char_list)
            self.send_command('HP', 1, self.area.hp_def)
            self.send_command('HP', 2, self.area.hp_pro)
            self.send_command('BN', self.area.background)
            self.send_command('LE', *self.area.get_evidence_list(self))
            self.send_command('MM', 1)
            self.send_command('OPPASS', fantacrypt.fanta_encrypt(self.server.config['guardpass']))
            if self.char_id is None:
                self.char_id = -1 # Set to a valid ID if still needed
            self.send_command('DONE')

        def char_select(self):
            self.char_id = -1
            self.send_done()

        def auth_mod(self, password):
            if self.is_mod:
                raise ClientError('Already logged in.')
            if password == self.server.config['modpass']:
                self.is_mod = True
                self.in_rp = False
            else:
                raise ClientError('Invalid password.')

        def auth_cm(self, password):
            if self.is_cm:
                raise ClientError('Already logged in.')
            if password == self.server.config['cmpass']:
                self.is_cm = True
                self.in_rp = False
            else:
                raise ClientError('Invalid password.')

        def auth_gm(self, password):
            if self.is_gm:
                raise ClientError('Already logged in.')
            if password == self.server.config['gmpass']:
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass1'] and ((datetime.datetime.today().weekday() == 6 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 0 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass2'] and ((datetime.datetime.today().weekday() == 0 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 1 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass3'] and ((datetime.datetime.today().weekday() == 1 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 2 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass4'] and ((datetime.datetime.today().weekday() == 2 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 3 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass5'] and ((datetime.datetime.today().weekday() == 3 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 4 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass6'] and ((datetime.datetime.today().weekday() == 4 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 5 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass7'] and ((datetime.datetime.today().weekday() == 5 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 6 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            else:
                raise ClientError('Invalid password.')

        def get_ip(self):
            return self.ipid

        def get_ipreal(self):
            return self.transport.get_extra_info('peername')[0]

        def get_char_name(self, char_id=None):
            if char_id is None:
                char_id = self.char_id

            if char_id == -1:
                return self.server.config['spectator_name']
            if char_id is None:
                return 'SERVER_SELECT'
            return self.server.char_list[char_id]

        def get_showname_history(self):
            info = '== Showname history of client {} =='.format(self.id)

            if len(self.showname_history) == 0:
                info += '\r\nClient has not changed their showname since joining the server.'
            else:
                for log in self.showname_history:
                    info += '\r\n*{}'.format(log)
            return info

        def change_position(self, pos=''):
            if pos not in ('', 'def', 'pro', 'hld', 'hlp', 'jud', 'wit'):
                raise ClientError('Invalid position. Possible values: def, pro, hld, hlp, jud, wit.')
            self.pos = pos

        def set_mod_call_delay(self):
            self.mod_call_time = round(time.time() * 1000.0 + 30000)

        def can_call_mod(self):
            return (time.time() * 1000.0 - self.mod_call_time) > 0

        def disemvowel_message(self, message):
            return self.remove_letters(message, 'aeiou')

        def disemconsonant_message(self, message):
            return self.remove_letters(message, 'bcdfghjklmnpqrstvwxyz')

        def remove_h_message(self, message):
            return self.remove_letters(message, 'h')

        def remove_letters(self, message, target):
            message = re.sub("[{}]".format(target), "", message, flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", message)

        def gimp_message(self, message):
            message = ['ERP IS BAN',
                       'I\'m fucking gimped because I\'m both autistic and a retard!',
                       'HELP ME',
                       'Boy, I sure do love Dia, the best admin, and the cutest!!!!!',
                       'I\'M SEVERELY AUTISTIC!!!!',
                       '[PEES FREELY]',
                       'KILL ME',
                       'I found this place on reddit XD',
                       '(((((case????)))))',
                       'Anyone else a fan of MLP?',
                       'does this server have sans from undertale?',
                       'what does call mod do',
                       'does anyone have a miiverse account?',
                       'Drop me a PM if you want to ERP',
                       'Join my discord server please',
                       'can I have mod pls?',
                       'why is everyone a missingo?',
                       'how 2 change areas?',
                       'does anyone want to check out my tumblr? :3',
                       '19 years of perfection, i don\'t play games to fucking lose',
                       'nah... your taunts are fucking useless... only defeat angers me... by trying to taunt just earns you my pitty',
                       'When do we remove dangits',
                       'MODS STOP GIMPING ME',
                       'Please don\'t say things like ni**er and f**k it\'s very rude and I don\'t like it',
                       'PLAY NORMIES PLS']
            return random.choice(message)

    def __init__(self, server):
        self.clients = set()
        self.server = server
        self.cur_id = [False] * self.server.config['playerlimit']

    def new_client(self, transport):
        cur_id = 0
        for i in range(self.server.config['playerlimit']):
            if not self.cur_id[i]:
                cur_id = i
                break
        c = self.Client(self.server, transport, cur_id, self.server.get_ipid(transport.get_extra_info('peername')[0]))
        self.clients.add(c)
        self.cur_id[cur_id] = True
        self.server.client_tasks[cur_id] = dict()
        return c

    def remove_client(self, client):
        # Clients who are following the now leaving client should no longer follow them
        try:
            client.followedby.unfollow_user()
        except AttributeError:
            pass
        self.cur_id[client.id] = False
        for task_id in self.server.client_tasks[client.id].keys(): # Cancel client's pending tasks
            self.server.get_task(client, [task_id]).cancel()
        self.clients.remove(client)

    def get_targets(self, client, key, value, local=False):
        #possible keys: ip, OOC, id, cname, ipid, hdid
        areas = None
        if local:
            areas = [client.area]
        else:
            areas = client.server.area_manager.areas
        targets = []
        if key == TargetType.ALL:
            for nkey in range(6):
                targets += self.get_targets(client, nkey, value, local)
        for area in areas:
            for c in area.clients:
                if key == TargetType.IP:
                    if value.lower().startswith(c.get_ipreal().lower()):
                        targets.append(c)
                elif key == TargetType.OOC_NAME:
                    if value.lower().startswith(c.name.lower()) and c.name:
                        targets.append(c)
                elif key == TargetType.CHAR_NAME:
                    if value.lower().startswith(c.get_char_name().lower()):
                        targets.append(c)
                elif key == TargetType.ID:
                    if c.id == value:
                        targets.append(c)
                elif key == TargetType.IPID:
                    if c.ipid == value:
                        targets.append(c)
        return targets

    def get_muted_clients(self):
        clients = []
        for client in self.clients:
            if client.is_muted:
                clients.append(client)
        return clients

    def get_ooc_muted_clients(self):
        clients = []
        for client in self.clients:
            if client.is_ooc_muted:
                clients.append(client)
        return clients

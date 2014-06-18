import asyncio
import autobahn.asyncio.websocket
import collections
import enums
import random
import sys
import ujson


class ClientManager:
    def __init__(self):
        self.next_client_id = 1
        self.client_id_to_client = {}
        self.usernames = set()
        self.next_game_id = 1
        self.game_id_to_game = {}
        self.pending_messages = []

    def open_client(self, client):
        client_id = self.next_client_id
        self.next_client_id += 1
        client.client_id = client_id
        self.client_id_to_client[client_id] = client
        messages_client = [[enums.CommandsToClient.SetClientId.value, client_id]]
        messages_other = []

        username = client.username
        if len(username) == 0 or len(username) > 32:
            messages_client.append([enums.CommandsToClient.FatalError.value, enums.FatalErrors.InvalidUsername.value])
            self.pending_messages.append(['client', client_id, None, messages_client])
            self.flush_pending_messages()
            client.sendClose()
            return
        elif username in self.usernames:
            messages_client.append([enums.CommandsToClient.FatalError.value, enums.FatalErrors.UsernameAlreadyInUse.value])
            self.pending_messages.append(['client', client_id, None, messages_client])
            self.flush_pending_messages()
            client.sendClose()
            return

        self.usernames.add(username)

        # tell client about all clients' data
        enum_set_client_id_to_data = enums.CommandsToClient.SetClientIdToData.value
        for client2 in self.client_id_to_client.values():
            messages_client.append([enum_set_client_id_to_data, client2.client_id, client2.username, client2.peer])

        # tell other clients about client's data
        messages_other.append([enum_set_client_id_to_data, client_id, username, client.peer])

        # tell client about all games
        set_game_state = enums.CommandsToClient.SetGameState.value
        client_index = enums.ScoreSheetPlayerIndexes.Client.value
        username_index = enums.ScoreSheetPlayerIndexes.Username.value
        set_game_player_username = enums.CommandsToClient.SetGamePlayerUsername.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value
        set_game_watcher_client_id = enums.CommandsToClient.SetGameWatcherClientId.value
        for game_id, game in self.game_id_to_game.items():
            messages_client.append([set_game_state, game_id, game.state])
            for player_id, player_datum in enumerate(game.score_sheet.player_data):
                if player_datum[client_index] is None:
                    messages_client.append([set_game_player_username, game_id, player_id, player_datum[username_index]])
                else:
                    messages_client.append([set_game_player_client_id, game_id, player_id, player_datum[client_index].client_id])
            for client_id2 in game.client_id_to_watcher_client.keys():
                messages_client.append([set_game_watcher_client_id, game_id, client_id2])

        self.pending_messages.append(['client', client_id, None, messages_client])
        self.pending_messages.append(['all', None, {client_id}, messages_other])

    def close_client(self, client):
        messages_all = []

        if client.game_id is not None:
            game = self.game_id_to_game[client.game_id]
            game.remove_client(client)
            self.pending_messages.extend(game.get_messages())

        del self.client_id_to_client[client.client_id]
        self.usernames.discard(client.username)

        enum_set_client_id_to_data = enums.CommandsToClient.SetClientIdToData.value
        messages_all.append([enum_set_client_id_to_data, client.client_id, None, None])

        self.pending_messages.append(['all', None, None, messages_all])

    def create_game(self, client):
        if client.game_id is None:
            game_id = self.next_game_id
            self.next_game_id += 1

            game = Game(game_id, client.username)
            self.game_id_to_game[game_id] = game

            game.create_game(client)

            self.pending_messages.extend(game.get_messages())

    def join_game(self, client, game_id):
        if client.game_id is None and game_id in self.game_id_to_game:
            game = self.game_id_to_game[game_id]
            game.join_game(client)
            self.pending_messages.extend(game.get_messages())

    def rejoin_game(self, client, game_id):
        if client.game_id is None and game_id in self.game_id_to_game:
            game = self.game_id_to_game[game_id]
            game.rejoin_game(client)
            self.pending_messages.extend(game.get_messages())

    def watch_game(self, client, game_id):
        if client.game_id is None and game_id in self.game_id_to_game:
            game = self.game_id_to_game[game_id]
            game.watch_game(client)
            self.pending_messages.extend(game.get_messages())

    def flush_pending_messages(self):
        empty_set = set()

        for target, target_id, exclude, messages in self.pending_messages:
            messages_json = ujson.dumps(messages)
            messages_json_bytes = messages_json.encode()
            print(target, target_id, exclude, '<-', messages_json)
            if exclude is None:
                exclude = empty_set
            if target == 'all':
                for client_id, client in self.client_id_to_client.items():
                    if client_id not in exclude:
                        client.sendMessage(messages_json_bytes)
            elif target == 'game':
                for client_id, client in self.game_id_to_game[target_id].client_id_to_client.items():
                    if client_id not in exclude:
                        client.sendMessage(messages_json_bytes)
            elif target == 'client':
                self.client_id_to_client[target_id].sendMessage(messages_json_bytes)
        del self.pending_messages[:]


client_manager = ClientManager()


class AcquireServerProtocol(autobahn.asyncio.websocket.WebSocketServerProtocol):
    def __init__(self):
        self.username = None
        self.client_id = None
        self.game_id = None
        self.player_id = None

    def onConnect(self, request):
        self.username = ' '.join(request.params.get('username', [''])[0].split())
        print('X', 'connect', self.peer, self.username)
        print()

    def onOpen(self):
        super().onOpen()
        client_manager.open_client(self)
        print(self.client_id, 'open', self.peer)
        client_manager.flush_pending_messages()
        print()

    def onClose(self, wasClean, code, reason):
        super().onClose(wasClean, code, reason)
        print(self.client_id, 'close')
        client_manager.close_client(self)
        client_manager.flush_pending_messages()
        print()

    def onMessage(self, payload, isBinary):
        super().onMessage(payload, isBinary)
        if not isBinary:
            try:
                message = payload.decode()
                print(self.client_id, '->', message)
                message = ujson.decode(message)
                method = getattr(self, 'onMessage' + enums.CommandsToServer(message[0]).name)
                arguments = message[1:]
            except Exception as e:
                print(e)
                self.sendClose()
                return

            try:
                method(*arguments)
                client_manager.flush_pending_messages()
                print()
            except TypeError as e:
                print(e)
                self.sendClose()
        else:
            self.sendClose()

    def onMessageCreateGame(self):
        client_manager.create_game(self)

    def onMessageJoinGame(self, game_id):
        client_manager.join_game(self, game_id)

    def onMessageRejoinGame(self, game_id):
        client_manager.rejoin_game(self, game_id)

    def onMessageWatchGame(self, game_id):
        client_manager.watch_game(self, game_id)


class GameBoard:
    def __init__(self):
        nothing = enums.GameBoardTypes.Nothing.value
        self.x_to_y_to_board_type = [[nothing for y in range(0, 9)] for x in range(0, 12)]
        self.board_type_to_coordinates = collections.defaultdict(set)
        self.board_type_to_coordinates[nothing].update((x, y) for x in range(0, 12) for y in range(0, 9))

        self.messages_game = []

    def set_cell(self, coordinates, board_type):
        x, y = coordinates
        old_board_type = self.x_to_y_to_board_type[x][y]
        self.board_type_to_coordinates[old_board_type].remove(coordinates)
        self.x_to_y_to_board_type[x][y] = board_type
        self.board_type_to_coordinates[board_type].add(coordinates)
        self.messages_game.append([enums.CommandsToClient.SetGameBoardType.value, x, y, board_type])


class ScoreSheet:
    def __init__(self, game_id):
        self.game_id = game_id

        self.player_data = []
        self.available = [25, 25, 25, 25, 25, 25, 25]
        self.chain_size = [0, 0, 0, 0, 0, 0, 0]
        self.price = [0, 0, 0, 0, 0, 0, 0]

        self.messages_all = []
        self.messages_game = []
        self.client_id_to_messages = collections.defaultdict(list)

    def add_player(self, client, starting_tile):
        self.player_data.append([0, 0, 0, 0, 0, 0, 0, 60, 60, client.username, starting_tile, client])
        self.player_data.sort(key=lambda x: x[enums.ScoreSheetPlayerIndexes.StartingTile.value])

        username_index = enums.ScoreSheetPlayerIndexes.Username.value
        starting_tile_index = enums.ScoreSheetPlayerIndexes.StartingTile.value
        client_index = enums.ScoreSheetPlayerIndexes.Client.value
        nothing_yet = enums.GameBoardTypes.NothingYet.value
        set_game_player_username = enums.CommandsToClient.SetGamePlayerUsername.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value
        set_game_board_type = enums.CommandsToClient.SetGameBoardType.value

        correct_player_id = 0
        for player_id, player_datum in enumerate(self.player_data):
            if player_datum[client_index] is not None:
                player_datum[client_index].player_id = correct_player_id
            correct_player_id += 1

        for player_id, player_datum in enumerate(self.player_data):
            player_datum = self.player_data[player_id]
            if player_id >= client.player_id:
                if player_datum[client_index] is None:
                    self.messages_all.append([set_game_player_username, self.game_id, player_id, player_datum[username_index]])
                else:
                    self.messages_all.append([set_game_player_client_id, self.game_id, player_id, player_datum[client_index].client_id])
            if player_id != client.player_id:
                starting_tile = player_datum[starting_tile_index]
                self.client_id_to_messages[client.client_id].append([set_game_board_type, starting_tile[0], starting_tile[1], nothing_yet])

    def readd_player(self, client):
        username_index = enums.ScoreSheetPlayerIndexes.Username.value
        client_index = enums.ScoreSheetPlayerIndexes.Client.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value

        for player_id, player_datum in enumerate(self.player_data):
            if client.username == player_datum[username_index]:
                client.player_id = player_id
                player_datum[client_index] = client
                self.messages_all.append([set_game_player_client_id, self.game_id, player_id, client.client_id])

    def remove_client(self, client):
        client_index = enums.ScoreSheetPlayerIndexes.Client.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value
        for player_id, player_datum in enumerate(self.player_data):
            if client is player_datum[client_index]:
                player_datum[client_index].game_id = None
                player_datum[client_index].player_id = None
                player_datum[client_index] = None
                self.messages_all.append([set_game_player_client_id, self.game_id, player_id, None])

    def is_username_in_game(self, username):
        username_index = enums.ScoreSheetPlayerIndexes.Username.value
        for player_datum in self.player_data:
            if username == player_datum[username_index]:
                return True
        return False


class TileBag:
    def __init__(self):
        tiles = [(x, y) for x in range(0, 12) for y in range(0, 9)]
        random.shuffle(tiles)
        self.tiles = tiles

    def get_tile(self):
        if len(self.tiles) > 0:
            return self.tiles.pop()
        else:
            return None

    def __len__(self):
        return len(self.tiles)


class Game:
    def __init__(self, game_id, creator_username):
        self.game_id = game_id
        self.creator_username = creator_username
        self.client_id_to_client = {}
        self.client_id_to_watcher_client = {}

        self.game_board = GameBoard()
        self.score_sheet = ScoreSheet(game_id)
        self.tile_bag = TileBag()

        self.state = enums.GameStates.Starting.value

        self.messages_all = []
        self.client_id_to_messages = collections.defaultdict(list)

        self.messages_all.append([enums.CommandsToClient.SetGameState.value, self.game_id, self.state])

    def create_game(self, client):
        if self.state == enums.GameStates.Starting.value:
            self.client_id_to_client[client.client_id] = client
            client.game_id = self.game_id
            starting_tile = self.tile_bag.get_tile()
            self.game_board.set_cell(starting_tile, enums.GameBoardTypes.NothingYet.value)
            self.score_sheet.add_player(client, starting_tile)

    def join_game(self, client):
        if self.state == enums.GameStates.Starting.value and not self.score_sheet.is_username_in_game(client.username):
            self.client_id_to_client[client.client_id] = client
            client.game_id = self.game_id
            starting_tile = self.tile_bag.get_tile()
            self.game_board.set_cell(starting_tile, enums.GameBoardTypes.NothingYet.value)
            self.score_sheet.add_player(client, starting_tile)

    def rejoin_game(self, client):
        if self.score_sheet.is_username_in_game(client.username):
            self.client_id_to_client[client.client_id] = client
            client.game_id = self.game_id
            self.score_sheet.readd_player(client)

    def watch_game(self, client):
        if not self.score_sheet.is_username_in_game(client.username):
            self.client_id_to_client[client.client_id] = client
            self.client_id_to_watcher_client[client.client_id] = client
            client.game_id = self.game_id
            self.messages_all.append([enums.CommandsToClient.SetGameWatcherClientId.value, self.game_id, client.client_id])

    def remove_client(self, client):
        if client.client_id in self.client_id_to_watcher_client:
            del self.client_id_to_watcher_client[client.client_id]
            self.messages_all.append([enums.CommandsToClient.ReturnWatcherToLobby.value, self.game_id, client.client_id])
        self.score_sheet.remove_client(client)
        del self.client_id_to_client[client.client_id]

    def get_messages(self):
        messages = []

        m = self.messages_all + self.score_sheet.messages_all
        if len(m) > 0:
            messages.append(['all', None, None, m])

        m = self.game_board.messages_game + self.score_sheet.messages_game
        if len(m) > 0:
            messages.append(['game', self.game_id, None, m])

        for client_id, m in self.score_sheet.client_id_to_messages.items():
            if client_id in self.client_id_to_messages:
                m.extend(self.client_id_to_messages[client_id])
            messages.append(['client', client_id, None, m])

        for client_id, m in self.client_id_to_messages.items():
            if client_id not in self.score_sheet.client_id_to_messages:
                messages.append(['client', client_id, None, m])

        del self.game_board.messages_game[:]
        del self.score_sheet.messages_all[:]
        del self.score_sheet.messages_game[:]
        self.score_sheet.client_id_to_messages.clear()
        del self.messages_all[:]
        self.client_id_to_messages.clear()

        return messages


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'debug':
        debug = True
    else:
        debug = False

    factory = autobahn.asyncio.websocket.WebSocketServerFactory('ws://127.0.0.1:9000', debug=debug)
    factory.protocol = AcquireServerProtocol

    loop = asyncio.get_event_loop()
    coro = loop.create_server(factory, '127.0.0.1', 9000)
    server = loop.run_until_complete(coro)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        loop.close()

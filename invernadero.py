"""
Invernadero Inteligente:

Código basado en el cliente gRPC de Google Assistant.
"""

import concurrent.futures
import json
import logging
import os
import os.path
import pathlib2 as pathlib
import sys
import time
import uuid
import busio
import digitalio
import board
import math
import subprocess
import emoji
from threading import Lock

#Para tareas periódicas
from timeloop import Timeloop
from datetime import timedelta

#Para los sensores
import adafruit_mcp3xxx.mcp3008 as MCP
from adafruit_mcp3xxx.analog_in import AnalogIn
import dht11
import RPi.GPIO as GPIO

#Asistente de Google
import click
import grpc
import google.auth.transport.grpc
import google.auth.transport.requests
import google.oauth2.credentials

#Bot telegram

from telegram.ext import Updater, MessageHandler, Filters

#Token del bot de Telegram
updater = Updater(token='1882106919:AAHMdEOvWboz7bPIX7_Rk45m2asSoCqzdjM', use_context=True)

dispatcher = updater.dispatcher

#Logging a consola
import logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                     level=logging.INFO)

#Función que se ejecuta al conectarse al bot de Telegram por primera vez.
def start(update, context):
    text = "Bienvenido al sistema de terrario autónomo."
    context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    
from telegram.ext import CommandHandler
start_handler = CommandHandler('start', start)
dispatcher.add_handler(start_handler)

#BlueDot
from bluedot import BlueDot
boton = BlueDot()

#Variables

status_hidratacion_tierra = 0
status_humedad_aire = 0
status_temp = 25
status_luz = 45
modo_automatico = False

#Para el modo autónomo, en caso de que no se llegue a estos valores se actúa.
hidratacion_minima_recomendable = 60
luz_minima_recomendable = 15

#GPIO

GPIO.setmode(GPIO.BCM)
GPIO.setup(16, GPIO.OUT, initial = GPIO.LOW)
GPIO.setup(26, GPIO.OUT, initial = GPIO.HIGH)
GPIO.setup(19, GPIO.OUT, initial = GPIO.LOW)
spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
cs = digitalio.DigitalInOut(board.D5)
mcp = MCP.MCP3008(spi, cs)
chan_light = AnalogIn(mcp, MCP.P0)
chan_hid = AnalogIn(mcp, MCP.P1)
dht11_instance = dht11.DHT11(pin = 4)

ref_voltage = 3.3

#Lecturas periódicas de sensores

tl = Timeloop()

#Para actualizar la temperatura mediante una lectura cada 5 segundos.
@tl.job(interval=timedelta(seconds=5))
def update_temp():
    global status_humedad_aire
    global status_temp
    result = dht11_instance.read()
    while not (result.is_valid()):
        result = dht11_instance.read()
    status_temp, status_humedad_aire = round(result.temperature), round(result.humidity)

#Para actualizar el nivel de luz mediante una lectura cada 5 segundos.
@tl.job(interval=timedelta(seconds=5))
def update_light():
    global status_luz
    #Regla de 3 para calcular el porcentaje.
    status_luz = round(100*(ref_voltage-chan_light.voltage)/ref_voltage)

#Para actualizar el nivel de hidratación de tierra mediante una lectura cada 5 segundos.
@tl.job(interval=timedelta(seconds=5))
def update_hydration():
    global status_hidratacion_tierra
    GPIO.output(16, 1)
    time.sleep(1)
    #Regla de 3 para calcular el porcentaje.
    status_hidratacion_tierra = round(100*(ref_voltage-chan_hid.voltage)/ref_voltage)
    GPIO.output(16, 0)

#Para revisar si el modo automático quiere regar o encender la luz, cada 10 segundos.
@tl.job(interval=timedelta(seconds=10))
def check_modo_automatico():
    global modo_automatico
    global luz_minima_recomendable
    global hidratacion_minima_recomendable
    if modo_automatico:
        if status_luz < luz_minima_recomendable:
            activar_luz()
        else:
            activar_luz("off")
        if status_hidratacion_tierra < hidratacion_minima_recomendable:
            activar_riego()

#Locks para acceso concurrente a los actuadores de riego y encender luz.
lock_agua = Lock()
lock_luz = Lock()

#Función para encender la bomba de agua (toggle)
def motor_toggle():
    GPIO.output(26, 0)
    time.sleep(0.1)
    GPIO.output(26, 1)

#Función para activar el riego un cierto tiempo.
def activar_riego():
    lock_agua.acquire()
    try:
        motor_toggle()
        time.sleep(1)
        motor_toggle()
        update_hydration()
    finally:
        lock_agua.release()

#Función para activar o desactivar la luz.
def activar_luz(modo = "on"):
    lock_luz.acquire()
    try:
        if modo == "on":
            GPIO.output(19, 1)
        else:
            GPIO.output(19, 0)
    finally:
        lock_luz.release()

dispatcher = updater.dispatcher

import logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                     level=logging.INFO)

#Comando de /status para Telegram
def status_cmd(update, context):
    text = "=====[ Status ]=====\n"
    text = text + f":thermometer: Temperatura:   {status_temp}°C\n"
    text = text + f":cloud_with_rain: Hidratación tierra:   {status_hidratacion_tierra}%\n"
    text = text + f":droplet: Humedad aire:   {status_humedad_aire}%\n"
    text = text + f":sun: Nivel de luz:   {status_luz}%"
    context.bot.send_message(chat_id=update.effective_chat.id, text=emoji.emojize(text))

status_handler = CommandHandler('status', status_cmd)
dispatcher.add_handler(status_handler)

#Comando para regar en Telegram
def regar_cmd(update, context):
    #Variable global que contiene el estado de riego
    global status_hidratacion_tierra
    
    if len(context.args) > 0:
        modo = context.args[0]
    else:
        modo = None
    text = ""
    #Si se activa el modo forzado de riego
    if modo == "forzoso":
        text = "Activando sistemas de riego forzoso :cloud_with_rain:"
        activar_riego()
    #Si la tierra esta seca
    elif status_hidratacion_tierra < hidratacion_minima_recomendable:
        text = f"La tierra está seca, regando :cloud_with_rain:"
        activar_riego()
    else:
        text = f":cross_mark:ERROR:cross_mark:: La tierra ya está regada. Si se desea regar de todas formas, activar modo forzoso."
    context.bot.send_message(chat_id=update.effective_chat.id, text=emoji.emojize(text))

regar_handler = CommandHandler('regar', regar_cmd)
dispatcher.add_handler(regar_handler)

#Comando para encender la luz en Telegram
def encender_cmd(update, context):
    text = "Encendiendo luz :sun:"
    activar_luz()
    context.bot.send_message(chat_id=update.effective_chat.id, text=emoji.emojize(text))

encender_handler = CommandHandler('encender', encender_cmd)
dispatcher.add_handler(encender_handler)

#Comando para apagar la luz en Telegram
def apagar_cmd(update, context):
    text = "Apagando luz :sun_behind_large_cloud:"
    activar_luz("off")
    context.bot.send_message(chat_id=update.effective_chat.id, text=emoji.emojize(text))

apagar_handler = CommandHandler('apagar', apagar_cmd)
dispatcher.add_handler(apagar_handler)

#Comando asociado al modo automático en Telegram
def auto_cmd(update, context):
    global modo_automatico
    
    text = ""
    if len(context.args) == 0:
        modo = None
    else:
        modo = context.args[0]
        
    if modo == "on":
        text = "Activando modo automático :robot:"
        modo_automatico = True
    elif modo == "off":
        text = "Desactivando modo automático :robot:"
        modo_automatico = False
    else:
        if modo_automatico:
            text ="El modo automático :robot: está activado."
        else:
            text = "El modo automático :robot: está desactivado."
    context.bot.send_message(chat_id=update.effective_chat.id, text=emoji.emojize(text))

auto_handler = CommandHandler('auto', auto_cmd)
dispatcher.add_handler(auto_handler)

#Cuando se le manda un comando inválido, responde con la lista de comandos válidos.
def unknown(update, context):
    errortext = emoji.emojize(""":cross_mark:ERROR:cross_mark:: Comando no reconocido.
    Comandos válidos:
    /status
    /apagar
    /encender
    /regar <forzoso>
    /auto <on/off>""")
    context.bot.send_message(chat_id=update.effective_chat.id, text=errortext)
unknown_handler = MessageHandler(Filters.command, unknown)
dispatcher.add_handler(unknown_handler)

updater.start_polling()

#Google Assistant

from google.assistant.embedded.v1alpha2 import (
    embedded_assistant_pb2,
    embedded_assistant_pb2_grpc
)
from tenacity import retry, stop_after_attempt, retry_if_exception
from gtts import gTTS

#Función TTS para dar una cadena y que la traduzca a un clip de voz por Google Assistant.
def speak(text):
    tts = gTTS(text=text, lang="es")
    filename = "__voice.mp3"
    tts.save(filename)
    with open(os.devnull, 'wb') as devnull:
        subprocess.check_call(['omxplayer', filename], stdout=devnull, stderr=subprocess.STDOUT)

#####
#Código del asistente de Google: Adaptado
#####

try:
    from . import (
        assistant_helpers,
        audio_helpers,
        browser_helpers,
        device_helpers
    )
except (SystemError, ImportError):
    import assistant_helpers
    import audio_helpers
    import browser_helpers
    import device_helpers


ASSISTANT_API_ENDPOINT = 'embeddedassistant.googleapis.com'
END_OF_UTTERANCE = embedded_assistant_pb2.AssistResponse.END_OF_UTTERANCE
DIALOG_FOLLOW_ON = embedded_assistant_pb2.DialogStateOut.DIALOG_FOLLOW_ON
CLOSE_MICROPHONE = embedded_assistant_pb2.DialogStateOut.CLOSE_MICROPHONE
PLAYING = embedded_assistant_pb2.ScreenOutConfig.PLAYING
DEFAULT_GRPC_DEADLINE = 60 * 3 + 5


class SampleAssistant(object):
    """Sample Assistant that supports conversations and device actions.

    Args:
      device_model_id: identifier of the device model.
      device_id: identifier of the registered device instance.
      conversation_stream(ConversationStream): audio stream
        for recording query and playing back assistant answer.
      channel: authorized gRPC channel for connection to the
        Google Assistant API.
      deadline_sec: gRPC deadline in seconds for Google Assistant API call.
      device_handler: callback for device actions.
    """

    def __init__(self, language_code, device_model_id, device_id,
                 conversation_stream, display,
                 channel, deadline_sec, device_handler):
        self.language_code = language_code
        self.device_model_id = device_model_id
        self.device_id = device_id
        self.conversation_stream = conversation_stream
        self.display = display

        # Opaque blob provided in AssistResponse that,
        # when provided in a follow-up AssistRequest,
        # gives the Assistant a context marker within the current state
        # of the multi-Assist()-RPC "conversation".
        # This value, along with MicrophoneMode, supports a more natural
        # "conversation" with the Assistant.
        self.conversation_state = None
        # Force reset of first conversation.
        self.is_new_conversation = True

        # Create Google Assistant API gRPC client.
        self.assistant = embedded_assistant_pb2_grpc.EmbeddedAssistantStub(
            channel
        )
        self.deadline = deadline_sec

        self.device_handler = device_handler


    def __enter__(self):
        return self

    def __exit__(self, etype, e, traceback):
        if e:
            return False
        self.conversation_stream.close()

    def is_grpc_error_unavailable(e):
        is_grpc_error = isinstance(e, grpc.RpcError)
        if is_grpc_error and (e.code() == grpc.StatusCode.UNAVAILABLE):
            logging.error('grpc unavailable error: %s', e)
            return True
        return False

    @retry(reraise=True, stop=stop_after_attempt(3),
           retry=retry_if_exception(is_grpc_error_unavailable))
    def assist(self):
        """Send a voice request to the Assistant and playback the response.

        Returns: True if conversation should continue.
        """
        continue_conversation = False
        device_actions_futures = []

        self.conversation_stream.start_recording()
        logging.info('Recording audio request.')

        def iter_log_assist_requests():
            for c in self.gen_assist_requests():
                assistant_helpers.log_assist_request_without_audio(c)
                yield c
            logging.debug('Reached end of AssistRequest iteration.')

        # This generator yields AssistResponse proto messages
        # received from the gRPC Google Assistant API.
        finished = False
        no_help = False
        for resp in self.assistant.Assist(iter_log_assist_requests(),
                                          self.deadline):
            assistant_helpers.log_assist_response_without_audio(resp)
            if resp.event_type == END_OF_UTTERANCE:
                logging.info('End of audio request detected.')
                logging.info('Stopping recording.')
                self.conversation_stream.stop_recording()
            if resp.speech_results:
                logging.info('Transcript of user request: "%s".',
                             ' '.join(r.transcript
                                      for r in resp.speech_results))
            if len(resp.audio_out.audio_data) > 0:
                if not self.conversation_stream.playing:
                    self.conversation_stream.stop_recording()
                    finished = True
            continue_conversation = False
            if resp.device_action.device_request_json:
                device_request = json.loads(
                    resp.device_action.device_request_json
                )
                fs = self.device_handler(device_request)
                if fs:
                    device_actions_futures.extend(fs)
            elif finished:
                no_help = True
        if len(device_actions_futures):
            logging.info('Waiting for device executions to complete.')
            concurrent.futures.wait(device_actions_futures)
        if(no_help):
            speak("Comando no reconocido.")
        logging.info('Assistant execution completed.')
        self.conversation_stream.stop_playback()
        return continue_conversation

    def gen_assist_requests(self):
        """Yields: AssistRequest messages to send to the API."""

        config = embedded_assistant_pb2.AssistConfig(
            audio_in_config=embedded_assistant_pb2.AudioInConfig(
                encoding='LINEAR16',
                sample_rate_hertz=self.conversation_stream.sample_rate,
            ),
            audio_out_config=embedded_assistant_pb2.AudioOutConfig(
                encoding='LINEAR16',
                sample_rate_hertz=self.conversation_stream.sample_rate,
                volume_percentage=self.conversation_stream.volume_percentage,
            ),
            dialog_state_in=embedded_assistant_pb2.DialogStateIn(
                language_code=self.language_code,
                conversation_state=self.conversation_state,
                is_new_conversation=self.is_new_conversation,
            ),
            device_config=embedded_assistant_pb2.DeviceConfig(
                device_id=self.device_id,
                device_model_id=self.device_model_id,
            )
        )
        if self.display:
            config.screen_out_config.screen_mode = PLAYING
        # Continue current conversation with later requests.
        self.is_new_conversation = False
        # The first AssistRequest must contain the AssistConfig
        # and no audio data.
        yield embedded_assistant_pb2.AssistRequest(config=config)
        for data in self.conversation_stream:
            # Subsequent requests need audio data, but not config.
            yield embedded_assistant_pb2.AssistRequest(audio_in=data)


@click.command()
@click.option('--api-endpoint', default=ASSISTANT_API_ENDPOINT,
              metavar='<api endpoint>', show_default=True,
              help='Address of Google Assistant API service.')
@click.option('--credentials',
              metavar='<credentials>', show_default=True,
              default=os.path.join(click.get_app_dir('google-oauthlib-tool'),
                                   'credentials.json'),
              help='Path to read OAuth2 credentials.')
@click.option('--project-id',
              metavar='<project id>',
              help=('Google Developer Project ID used for registration '
                    'if --device-id is not specified'))
@click.option('--device-model-id',
              metavar='<device model id>',
              help=(('Unique device model identifier, '
                     'if not specifed, it is read from --device-config')))
@click.option('--device-id',
              metavar='<device id>',
              help=(('Unique registered device instance identifier, '
                     'if not specified, it is read from --device-config, '
                     'if no device_config found: a new device is registered '
                     'using a unique id and a new device config is saved')))
@click.option('--device-config', show_default=True,
              metavar='<device config>',
              default=os.path.join(
                  click.get_app_dir('googlesamples-assistant'),
                  'device_config.json'),
              help='Path to save and restore the device configuration')
@click.option('--lang', show_default=True,
              metavar='<language code>',
              default='es-ES',#'en-US',
              help='Language code of the Assistant')
@click.option('--display', is_flag=True, default=False,
              help='Enable visual display of Assistant responses in HTML.')
@click.option('--verbose', '-v', is_flag=True, default=False,
              help='Verbose logging.')
@click.option('--input-audio-file', '-i',
              metavar='<input file>',
              help='Path to input audio file. '
              'If missing, uses audio capture')
@click.option('--output-audio-file', '-o',
              metavar='<output file>',
              help='Path to output audio file. '
              'If missing, uses audio playback')
@click.option('--audio-sample-rate',
              default=audio_helpers.DEFAULT_AUDIO_SAMPLE_RATE,
              metavar='<audio sample rate>', show_default=True,
              help='Audio sample rate in hertz.')
@click.option('--audio-sample-width',
              default=audio_helpers.DEFAULT_AUDIO_SAMPLE_WIDTH,
              metavar='<audio sample width>', show_default=True,
              help='Audio sample width in bytes.')
@click.option('--audio-iter-size',
              default=audio_helpers.DEFAULT_AUDIO_ITER_SIZE,
              metavar='<audio iter size>', show_default=True,
              help='Size of each read during audio stream iteration in bytes.')
@click.option('--audio-block-size',
              default=audio_helpers.DEFAULT_AUDIO_DEVICE_BLOCK_SIZE,
              metavar='<audio block size>', show_default=True,
              help=('Block size in bytes for each audio device '
                    'read and write operation.'))
@click.option('--audio-flush-size',
              default=audio_helpers.DEFAULT_AUDIO_DEVICE_FLUSH_SIZE,
              metavar='<audio flush size>', show_default=True,
              help=('Size of silence data in bytes written '
                    'during flush operation'))
@click.option('--grpc-deadline', default=DEFAULT_GRPC_DEADLINE,
              metavar='<grpc deadline>', show_default=True,
              help='gRPC deadline in seconds')
@click.option('--once', default=False, is_flag=True,
              help='Force termination after a single conversation.')
def main(api_endpoint, credentials, project_id,
         device_model_id, device_id, device_config,
         lang, display, verbose,
         input_audio_file, output_audio_file,
         audio_sample_rate, audio_sample_width,
         audio_iter_size, audio_block_size, audio_flush_size,
         grpc_deadline, once, *args, **kwargs):
    """Samples for the Google Assistant API.

    Examples:
      Run the sample with microphone input and speaker output:

        $ python -m googlesamples.assistant

      Run the sample with file input and speaker output:

        $ python -m googlesamples.assistant -i <input file>

      Run the sample with file input and output:

        $ python -m googlesamples.assistant -i <input file> -o <output file>
    """
    # Setup logging.
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)

    # Load OAuth 2.0 credentials.
    try:
        with open(credentials, 'r') as f:
            credentials = google.oauth2.credentials.Credentials(token=None,
                                                                **json.load(f))
            http_request = google.auth.transport.requests.Request()
            credentials.refresh(http_request)
    except Exception as e:
        logging.error('Error loading credentials: %s', e)
        logging.error('Run google-oauthlib-tool to initialize '
                      'new OAuth 2.0 credentials.')
        sys.exit(-1)

    # Create an authorized gRPC channel.
    grpc_channel = google.auth.transport.grpc.secure_authorized_channel(
        credentials, http_request, api_endpoint)
    logging.info('Connecting to %s', api_endpoint)

    # Configure audio source and sink.
    audio_device = None
    if input_audio_file:
        audio_source = audio_helpers.WaveSource(
            open(input_audio_file, 'rb'),
            sample_rate=audio_sample_rate,
            sample_width=audio_sample_width
        )
    else:
        audio_source = audio_device = (
            audio_device or audio_helpers.SoundDeviceStream(
                sample_rate=audio_sample_rate,
                sample_width=audio_sample_width,
                block_size=audio_block_size,
                flush_size=audio_flush_size
            )
        )
    if output_audio_file:
        audio_sink = audio_helpers.WaveSink(
            open(output_audio_file, 'wb'),
            sample_rate=audio_sample_rate,
            sample_width=audio_sample_width
        )
    else:
        audio_sink = audio_device = (
            audio_device or audio_helpers.SoundDeviceStream(
                sample_rate=audio_sample_rate,
                sample_width=audio_sample_width,
                block_size=audio_block_size,
                flush_size=audio_flush_size
            )
        )
    # Create conversation stream with the given audio source and sink.
    conversation_stream = audio_helpers.ConversationStream(
        source=audio_source,
        sink=audio_sink,
        iter_size=audio_iter_size,
        sample_width=audio_sample_width,
    )

    if not device_id or not device_model_id:
        try:
            with open(device_config) as f:
                device = json.load(f)
                device_id = device['id']
                device_model_id = device['model_id']
                logging.info("Using device model %s and device id %s",
                             device_model_id,
                             device_id)
        except Exception as e:
            logging.warning('Device config not found: %s' % e)
            logging.info('Registering device')
            if not device_model_id:
                logging.error('Option --device-model-id required '
                              'when registering a device instance.')
                sys.exit(-1)
            if not project_id:
                logging.error('Option --project-id required '
                              'when registering a device instance.')
                sys.exit(-1)
            device_base_url = (
                'https://%s/v1alpha2/projects/%s/devices' % (api_endpoint,
                                                             project_id)
            )
            device_id = str(uuid.uuid1())
            payload = {
                'id': device_id,
                'model_id': device_model_id,
                'client_type': 'SDK_SERVICE'
            }
            session = google.auth.transport.requests.AuthorizedSession(
                credentials
            )
            r = session.post(device_base_url, data=json.dumps(payload))
            if r.status_code != 200:
                logging.error('Failed to register device: %s', r.text)
                sys.exit(-1)
            logging.info('Device registered: %s', device_id)
            pathlib.Path(os.path.dirname(device_config)).mkdir(exist_ok=True)
            with open(device_config, 'w') as f:
                json.dump(payload, f)

    device_handler = device_helpers.DeviceRequestHandler(device_id)

    @device_handler.command('com.invernadero.commands.status')
    def status_invernadero(dummy):
        global status_luz
        global status_temp
        global status_humedad_aire
        global status_hidratacion_tierra
        s = ""
        s1 = str(status_luz)
        s2, s3 = status_temp, status_humedad_aire
        s4 = str(status_hidratacion_tierra)
        s = s+ "El nivel de luz es: " + s1 + "%.\n"
        s = s+ "La temperatura es: " + str(s2) + " grados centígrados.\n"
        s = s+ "El nivel de hidratación del aire es: " + str(s3) + "%.\n"
        s = s+ "El nivel de hidratación de tierra es: " + s4 + "%."
        speak(s)
        logging.info(s)

    @device_handler.command('com.invernadero.commands.light_on')
    def light_on(dummy):
        speak("Encendiendo luz.")
        activar_luz()
        logging.info("Encendiendo luz.")
    
    @device_handler.command('com.invernadero.commands.light_off')
    def light_off(dummy):
        speak("Apagando luz.")
        activar_luz("off")
        logging.info("Apagando luz.")

    @device_handler.command('com.invernadero.commands.water')
    def water(mode):
        if mode == "force":
            speak("Activando el riego forzado.")
            activar_riego()
        elif status_hidratacion_tierra < hidratacion_minima_recomendable:
            speak("Nivel de hidratación insuficiente. Regando.")
            activar_riego()
        else:
            speak("No es necesario regar las plantas. Nivel de hidratación suficiente.")

    @device_handler.command('com.invernadero.commands.setmode')
    def setmode(mode):
        global modo_automatico
        if mode == "on":
            modo_automatico = True
            speak("Activando el modo automático.")
        elif mode == "off":
            modo_automatico = False
            speak("Desactivando el modo automático.")
        else:
            if modo_automatico:
                speak("El modo automático está activado.")
            else:
                speak("El modo automático está desactivado.")


    with SampleAssistant(lang, device_model_id, device_id,
                         conversation_stream, display,
                         grpc_channel, grpc_deadline,
                         device_handler) as assistant:
        # If file arguments are supplied:
        # exit after the first turn of the conversation.
        if input_audio_file or output_audio_file:
            assistant.assist()
            return

        # If no file arguments supplied:
        # keep recording voice requests using the microphone
        # and playing back assistant response using the speaker.
        # When the once flag is set, don't wait for a trigger. Otherwise, wait.
        wait_for_user_trigger = not once
        while True:
            if wait_for_user_trigger: #Modificado para esperar señal del BlueDot en vez de teclado.
                print("Esperando señal del BlueDot...")
                boton.wait_for_press()
            continue_conversation = assistant.assist()
            # wait for user trigger if there is no follow-up turn in
            # the conversation.
            wait_for_user_trigger = not continue_conversation

            # If we only want one conversation, break.
            if once and (not continue_conversation):
                break


#Ciclo principal del programa.
if __name__ == '__main__':
    tl.start()
    main()
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            tl.stop()
            break

"""Medidores de nível ao vivo (opcional: precisa do GStreamer via PyGObject).

Se o GStreamer ou o plugin pulsesrc não estiverem disponíveis, AVAILABLE fica False
e a interface simplesmente esconde os medidores.
"""

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    AVAILABLE = Gst.ElementFactory.find("pulsesrc") is not None \
        and Gst.ElementFactory.find("level") is not None
except (ImportError, ValueError):
    AVAILABLE = False


class LevelMeter(object):
    """Reads a PulseAudio/PipeWire source and calls on_level(0..1) about 20x per second."""

    def __init__(self, on_level):
        self.on_level = on_level
        self.pipeline = None
        self.device = None

    def start(self, device):
        if not AVAILABLE:
            return
        if self.pipeline is not None and self.device == device:
            return
        self.stop()
        self.device = device
        desc = ("pulsesrc device={} client-name=AudioMic-medidor "
                "! audioconvert ! level interval=50000000 post-messages=true "
                "! fakesink sync=false").format(device)
        try:
            self.pipeline = Gst.parse_launch(desc)
        except Exception:
            self.pipeline = None
            return
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::element", self._on_message)
        bus.connect("message::error", lambda *a: self.stop())
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline = None
        self.device = None
        self.on_level(0.0)

    def _on_message(self, bus, msg):
        st = msg.get_structure()
        if st is None or st.get_name() != "level":
            return
        try:
            peaks = st.get_value("peak")
        except TypeError:
            return
        if not peaks:
            return
        db = max(peaks)
        self.on_level(max(0.0, min(1.0, (db + 50.0) / 50.0)))

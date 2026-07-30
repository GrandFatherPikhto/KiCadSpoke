#!.venv/bin/python
"""
kicadspoke_cli.py — main entry point for KiCadSpoke.

Usage:
    python kicadspoke_cli.py apply config.yaml [--dry-run] [--timeout-ms 20000] [--batch-size 10]
    python kicadspoke_cli.py undo [--verbose]
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

# Translated/typographic text (em dashes, non-breaking hyphens, degree signs, ...)
# can't be encoded by legacy console codepages (e.g. Windows cp1251/cp866), which
# crashes the logging StreamHandler mid-run with UnicodeEncodeError.  UTF-8 can
# encode any codepoint, so this removes the crash regardless of the terminal;
# whether it also *displays* correctly still depends on the terminal itself.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from kicadspoke.config import load_config, RuntimeContext
from kicadspoke.apply_pipeline import ApplyPipeline, cmd_apply
from kicadspoke.cli_extract import cmd_extract, load_profile, _CLONE_EXTRACT_PROFILE_KNOWN_KEYS
from kicadspoke.exceptions import PlacerError
from kicadspoke.logging_setup import setup_logging
from kicadspoke.undo import undo_last_operation
from kicadspoke.constants import DEFAULT_TIMEOUT_MS, DEFAULT_BATCH_SIZE
from kicadspoke.i18n import _
from kipy.errors import ApiError, ApiStatusCode


def cmd_undo(args):
    """Undo the last operation."""
    logger = logging.getLogger(__name__)
    log_dir = Path("logs")
    if not log_dir.exists():
        logger.error(_("logs directory not found."))
        return

    files = sorted(log_dir.glob("operation_*.json"), key=lambda p: p.stat().st_ctime)
    if not files:
        logger.error(_("No operation files to undo."))
        return

    last_file = files[-1]
    logger.info(_("Undoing operation from {file}").format(file=last_file.name))
    success = undo_last_operation(last_file)
    if success:
        logger.info(_("✅ Operation successfully undone."))
    else:
        logger.error(_("❌ Failed to undo operation."))


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ['apply', 'undo', 'extract', 'clone-extract']:
        sys.argv.insert(1, 'apply')

    parser = argparse.ArgumentParser(
        description=_("KiCad Decap Placer – capacitor placement (manual strategy)"),
        epilog=_("Example: kicadspoke_cli.py config.yaml --dry-run")
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help=_("Subcommand"))

    apply_parser = subparsers.add_parser("apply", help=_("Apply placement"))
    apply_parser.add_argument("config", help=_("YAML configuration file"))
    apply_parser.add_argument("--dry-run", action="store_true", help=_("Only print the plan, do not apply"))
    apply_parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help=_("IPC timeout in ms"))
    apply_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=_("Batch size for commits"))
    apply_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    apply_parser.add_argument("--log-file", help=_("File to save logs"))
    apply_parser.add_argument("--no-collision-check", action="store_true", help=_("Disable collision checking"))
    apply_parser.add_argument("--no-selection", action="store_true",
                              help=_("Ignore the current PCB editor selection for the whole run — "
                                     "role-based ClonePlacements (role: without nets:/params:) and "
                                     "ambiguity narrowing normally fall back to whatever is selected in "
                                     "KiCad; a stray leftover selection then either fatals or silently "
                                     "changes the resolved candidate. With this flag every such lookup "
                                     "behaves as if nothing were selected."))
    apply_parser.add_argument("--collision-margin", type=float, default=0.2, help=_("Extra clearance for collision check in mm"))
    apply_parser.add_argument("--only", action="append", metavar="NAME",
                              help=_("Process only rules/clone_placements/thermal_via_array with this "
                                     "identity (rule name if set, else its net; clone_placement/"
                                     "thermal_via_array name). Repeatable and/or comma-separated "
                                     "(--only a,b --only c). Everything else is ignored in this run."))
    apply_parser.add_argument("--cluster", action="append", metavar="PATH",
                              help=_("Process only spokes/clone_placements/thermal_via_array whose "
                                     "Cluster (anchor_cluster / spoke cluster) matches this path or "
                                     "prefix (segment-wise, e.g. 'Channel_0' also matches "
                                     "'Channel_0/DAC_OA'). Repeatable and/or comma-separated. "
                                     "Combines with --only via AND (run apply twice for OR)."))

    undo_parser = subparsers.add_parser("undo", help=_("Undo last operation"))
    undo_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    undo_parser.add_argument("--log-file", help=_("File to save logs"))

    clone_extract = subparsers.add_parser(
        "clone-extract",
        help=_("Snapshot a channel to YAML (file‑based cloner, no IPC)")
    )
    clone_extract.add_argument("--net", help=_("Path to .net file"))
    clone_extract.add_argument("--pcb", help=_("Path to .kicad_pcb file"))
    clone_extract.add_argument("--channel", help=_("Channel name, e.g. Channel_0"))
    clone_extract.add_argument("--output", help=_("YAML snapshot file"))
    clone_extract.add_argument("--profiles", metavar="FILE",
                               help=_("YAML file with named profiles for clone-extract"))
    clone_extract.add_argument("--profile", metavar="NAME",
                               help=_("Take net/pcb/channel/output from profile NAME in --profiles file "
                                      "(cannot combine with explicit flags)"))
    clone_extract.add_argument("-v", "--verbose", action="store_true", help=_("Verbose output"))

    extract_parser = subparsers.add_parser("extract", help=_("Extract spoke template from current selection"))
    extract_parser.add_argument("--name", help=_("Template name (key in templates:)"))
    extract_parser.add_argument("--output", help=_("Output YAML/JSON file"))
    extract_parser.add_argument("--profiles", metavar="FILE",
                                help=_("YAML file with named profiles for extract"))
    extract_parser.add_argument("--profile", metavar="NAME",
                                help=_("Take name/output/param/net-template/origin-by-* from profile NAME "
                                       "in --profiles file (cannot combine with explicit flags)"))
    extract_parser.add_argument("--timeout-ms", type=int, default=20000, help=_("IPC timeout in ms"))
    extract_parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    extract_parser.add_argument("--log-file", help=_("File to save logs"))
    extract_parser.add_argument("--param", action="append", metavar="KEY=VALUE",
                                help=_("Parameter for --net-template verification (e.g. channel=1); "
                                       "can be repeated; not written to template, only round-trip check"))
    extract_parser.add_argument("--net-template", action="append", metavar="LITERAL=PATTERN",
                                help=_("Mapping real net -> pattern with {placeholder} "
                                       "(e.g. 'DAC1_DB1=DAC{channel}_DB1'); can be repeated; "
                                       "fills net_template for roles and parametrizes via.net at extraction"))
    extract_parser.add_argument("--net-template-role", action="append", metavar="ROLE=LITERAL",
                                help=_("For components with multiple nets from --net-template on pads "
                                       "(ferrite/inductor/fuse between two rails) – explicitly tells "
                                       "which net is the role's net_template (e.g. 'PI_FILTER_FB=+5V_DIRTY'); "
                                       "without this such roles get empty net_template and need manual edit. "
                                       "Fatal if the role does not actually have that net on its pads, "
                                       "or if the literal is not registered in --net-template/params."))
    origin_group = extract_parser.add_mutually_exclusive_group()
    origin_group.add_argument("--origin-by-via-net", metavar="NET",
                              help=_("Template origin — position of via on this net (instead of bbox); "
                                     "fatal if no such via in selection or more than one"))
    origin_group.add_argument("--origin-by-component-role", metavar="ROLE",
                              help=_("Template origin — position of component with this role "
                                     "(instead of bbox); fatal if role not found in selection"))
    extract_parser.add_argument("--origin-by-component-pad", metavar="PAD",
                                help=_("Refine --origin-by-component-role: origin is the position of "
                                       "the specific pad of that component, not its centre. "
                                       "Fatal without --origin-by-component-role."))

    args = parser.parse_args()

    # Load config early (only for apply) to pick up log_file from config if --log-file not given.
    cfg = None
    ctx = None
    if args.command == "apply":
        try:
            cfg, ctx = load_config(args.config)
        except Exception:
            cfg = None
            ctx = None

    log_file = getattr(args, "log_file", None) or (cfg.log_file if cfg else None)
    setup_logging(verbose=getattr(args, "verbose", False), log_file=log_file)

    try:
        if args.command == "apply":
            cmd_apply(args, cfg=cfg, ctx=ctx)
        elif args.command == "undo":
            cmd_undo(args)
        elif args.command == "clone-extract":
            direct_given = bool(args.net or args.pcb or args.channel or args.output)
            if args.profile and direct_given:
                sys.exit(_("[error] --profile cannot be combined with --net/--pcb/--channel/--output"))
            if args.profile:
                if not args.profiles:
                    sys.exit(_("[error] --profile given without --profiles (profiles file)"))
                prof = load_profile(args.profiles, "clone_profiles", args.profile,
                                    known_keys=_CLONE_EXTRACT_PROFILE_KNOWN_KEYS)
                for required in ("net", "pcb", "channel", "output"):
                    if required not in prof:
                        sys.exit(_("[error] profile {profile!r} missing required field {field!r}")
                                 .format(profile=args.profile, field=required))
                net_path, pcb_path, channel, output = prof["net"], prof["pcb"], prof["channel"], prof["output"]
            else:
                if not (args.net and args.pcb and args.channel and args.output):
                    sys.exit(_("[error] need --net/--pcb/--channel/--output (or --profiles/--profile)"))
                net_path, pcb_path, channel, output = args.net, args.pcb, args.channel, args.output
            from kicadspoke.cloner.extract import extract_channel
            d = extract_channel(net_path, pcb_path, channel, output)
            s = d['summary']
            print(_("[{channel}] footprints: {fp}, segments: {seg}, vias: {vias} -> {output}")
                  .format(channel=channel, fp=s['footprints'], seg=s['segments'],
                          vias=s['vias'], output=output))
        elif args.command == "extract":
            cmd_extract(args)
        else:
            parser.print_help()
            sys.exit(1)
    except PlacerError as e:
        logging.error(_("Error: {e}").format(e=e))
        sys.exit(1)
    except ApiError as e:
        if e.code == ApiStatusCode.AS_BUSY:
            logging.error(
                _("KiCad is busy and cannot respond right now. Usually this means an unfinished "
                  "tool is active in the GUI (dimensioning, interactive routing, move tool, etc.) — "
                  "finish it (Esc or right-click -> Cancel) and run the command again. "
                  "The board was not modified.")
            )
        else:
            logging.error(_("KiCad returned API error: {e}").format(e=e))
        sys.exit(1)
    except Exception as e:
        logging.exception(_("Unexpected error"))
        sys.exit(2)


if __name__ == "__main__":
    main()

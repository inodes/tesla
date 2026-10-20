"""The interactive terminal browser for drives and footage (day and month menus, timeline, drill-downs). Moved unchanged out of analyse_drives.py."""

from lib.table_formatter import bold_headers
from datetime import datetime
from collections import defaultdict
from lib.table_formatter import display_len
from lib.table_formatter import format_box_line
from lib.fuel_common import format_drive_fuel_comparison
from lib.table_formatter import format_row
from lib.sei_inspector import format_stream_banner
from lib.table_formatter import format_title_line
from lib.sei_inspector import inspect_mp4_clip
import json
import os
from lib.table_formatter import pad_display
import re
import sys
from datetime import timedelta
from lib.analysis_deepdive_charts import try_open_in_browser
from lib.footage_keeping import build_timeline_footage_packages, close_preview_process, copy_clips_locally, ensure_package_sizes, flag_footage_packages_for_deletion, get_flagged_tombstones, get_primary_preview_clip, get_tombstone_lookup_sets, is_package_flagged, load_tombstones, open_footage_package, parse_tombstone_datetime, purge_flagged_tombstones_across_drives, render_staging_preview_table, resolve_master_teslacam_dir, save_tombstones, unflag_footage_packages
from lib.drives.helpers import clean_event_reason, extract_notable_destinations, format_drive_efficiency, format_duration_short, is_home_event

def wrap_text_display(s, max_width):
    if display_len(s) <= max_width:
        return [s]
    if "; " in s:
        parts = s.split("; ")
        lines = []
        for p in parts:
            lines.extend(wrap_text_display(p, max_width))
        return lines
    words = s.split(" ")
    lines = []
    current = ""
    for w in words:
        candidate = f"{current} {w}".strip() if current else w
        if display_len(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [s]


def display_flagged_deletions_table(analyzer):
    """Renders structured Unicode table of all active tombstones sorted by date and grouped by month."""
    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    flagged = get_flagged_tombstones(master_dir)
    n_drives = max(1, len(analyzer.teslacam_dirs))
    
    if not flagged:
        total_inner = 76
        print(f"\n┌{'─'*total_inner}┐")
        print(format_title_line(" 🗑️  ACTIVE TOMBSTONES (Pending Multi-Drive Cleanup)", total_inner, "left"))
        print(format_box_line("├", "─", "┤", [total_inner]))
        print(f"│{pad_display(' ℹ️  No active tombstones (all drives are fully synchronized).', total_inner, 'left')}│")
        print(format_box_line("└", "─", "┘", [total_inner]))
        return []

 # Parse and sort all items chronologically (newest first)
    parsed_items = []
    for k, info in flagged.items():
        dt = parse_tombstone_datetime(k, info)
        parsed_items.append((k, info, dt))

    parsed_items.sort(key=lambda x: x[2], reverse=True)

 # Group by month (e.g. '2026-09')
    months_grouped = {}
    for k, info, dt in parsed_items:
        m_key = dt.strftime("%Y-%m") if dt != datetime.min else "Unknown"
        months_grouped.setdefault(m_key, []).append((k, info, dt))

    max_k_len = max((display_len(k) for k, _, _ in parsed_items), default=30)
    w_idx = max(5, max((display_len(f" [{idx+1}]") + 1 for idx in range(len(parsed_items))), default=5))
    w_key = max(30, min(45, max_k_len + 2))
    w_type = 10
    w_files = 7
    w_size = 11
    w_drives = 22
    w_date = 18
    
    col_widths = [w_idx, w_key, w_type, w_files, w_size, w_drives, w_date]
    col_aligns = ["center", "left", "left", "right", "right", "left", "left"]
    
    total_inner = sum(col_widths) + len(col_widths) - 1
    title = f" 🗑️  ACTIVE TOMBSTONES (Pending Multi-Drive Cleanup — {len(parsed_items)} Items Across {len(months_grouped)} Month{'s' if len(months_grouped) != 1 else ''})"
    t_len = display_len(title)
    if t_len >= total_inner:
        w_key += (t_len - total_inner) + 3
        col_widths[1] = w_key
        total_inner = sum(col_widths) + len(col_widths) - 1

    print(f"\n┌{'─'*total_inner}┐")
    print(format_title_line(title, total_inner, "left"))
    print(format_box_line("├", "┬", "┤", col_widths))
    print(format_row(["#", " Package / Relative Path", " Type", "Files ", "Est. Size ", " Pending Drives", " Deleted Date"], col_widths, col_aligns))
    
    tot_bytes = 0
    tot_files = 0
    ordered_items = []
    row_num = 1
    
    for m_key, m_items in months_grouped.items():
        m_bytes = sum(inf.get("est_bytes", 0) for _, inf, _ in m_items)
        m_mb = m_bytes / (1024 * 1024)
        m_sz = f"{m_mb/1024:.2f} GB" if m_mb >= 1024 else f"{m_mb:.0f} MB"
        try:
            m_dt = datetime.strptime(m_key + "-01", "%Y-%m-%d")
            m_label = m_dt.strftime("%B %Y")
        except Exception:
            m_label = m_key
        m_banner = f" 🗓️  {m_label} ({len(m_items)} package{'s' if len(m_items) != 1 else ''} — ~{m_sz})"
        
        print(format_box_line("├", "┴", "┤", col_widths))
        print(f"│{pad_display(m_banner, total_inner, 'left')}│")
        print(format_box_line("├", "┬", "┤", col_widths))
        
        for key, info, dt in m_items:
            cat = info.get("cat", "Saved")
            icon = "🔄" if cat == "Recent" else ("💾" if cat == "Saved" else ("🔴" if cat == "Sentry" else "📁"))
            type_str = f" {icon} {cat}"
            f_cnt = info.get("files_count", len(info.get("files", [])))
            sz_b = info.get("est_bytes", 0)
            tot_bytes += sz_b
            tot_files += f_cnt
            
            sz_mb = sz_b / (1024 * 1024)
            sz_str = f"{sz_mb/1024:.1f} GB" if sz_mb >= 1024 else f"{sz_mb:.0f} MB"
            
            pending_list = info.get("pending_drives", [])
            pending_str = ", ".join(d.replace("VOL_", "") for d in pending_list) if pending_list else "None"
            fl_dt = info.get("deleted_at", "-")[:16].replace("T", " ")
            
            c_idx = f"[{row_num}]"
            k_disp = key if display_len(key) <= (w_key - 2) else key[:w_key - 5] + "..."
            c_key = f" {k_disp}"
            c_files = f"{f_cnt} "
            c_size = f"{sz_str} "
            c_drives = f" {pending_str}"
            c_date = f" {fl_dt}"
            
            cells = [c_idx, c_key, type_str, c_files, c_size, c_drives, c_date]
            print(format_row(cells, col_widths, col_aligns))
            ordered_items.append((key, info))
            row_num += 1
        
    print(format_box_line("├", "┴", "┤", col_widths))
    
    tot_mb = tot_bytes / (1024 * 1024)
    tot_sz_str = f"{tot_mb/1024:.2f} GB" if tot_mb >= 1024 else f"{tot_mb:.1f} MB"
    
    sum_line = f" Total Active Tombstones: {len(parsed_items)} package(s), {tot_files} files (~{tot_sz_str} reclaimable across secondary drives)"
    print(f"│{pad_display(sum_line, total_inner, 'left')}│")
    print(format_box_line("└", "─", "┘", [total_inner]))
    print()
    return ordered_items


def display_flagged_deletions_menu(analyzer):
    """Interactive management menu for staged/flagged deletions."""
    while True:
        items = display_flagged_deletions_table(analyzer)
        if not items or not sys.stdin.isatty():
            if sys.stdin.isatty():
                input("Press Enter to return to timeline: ")
            break
            
        try:
            print("\nAction:")
            print("  [u <#>] Unflag footage package from deletion (e.g. 'u 1', 'u 2-4', 'u all')")
            print("  [p]     Purge secondary drives now across mounted TeslaCam archives")
            print("  [b]     Back to timeline")
            print("  [q]     Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["p", "purge"]:
                try:
                    conf = input("Type 'yes' or 'y' to scrub and purge all tombstone footage from secondary drives: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    print("\nPurge cancelled.\n")
                    continue
                    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
                    n_del, b_del, n_ret = purge_flagged_tombstones_across_drives(analyzer.teslacam_dirs, analyzer.repo_root)
                    if n_del > 0 or n_ret > 0:
                        del_mb = b_del / (1024 * 1024)
                        del_sz = f"{del_mb/1024:.1f} GB" if del_mb >= 1024 else f"{del_mb:.0f} MB"
                        print(f"\n✔ Successfully purged {n_del} file(s) ({del_sz}) across secondary drive(s).")
                        print(f"✔ Retired and cleaned up {n_ret} tombstone(s) in .TeslaCam_tombstones.json.\n")
                    else:
                        data = load_tombstones(master_dir)
                        tombs = data.get("tombstones", {})
                        if tombs:
                            all_pending = set()
                            for t in tombs.values():
                                all_pending.update(t.get("pending_drives", []))
                            p_str = ", ".join(d.replace("VOL_", "") for d in sorted(all_pending)) if all_pending else "None"
                            print(f"\n>>> No connected secondary drives have pending files to purge.")
                            print(f"    ({len(tombs)} active tombstone(s) pending for disconnected drive(s): {p_str})")
                            try:
                                ans_clear = input(f"Would you like to clear/retire these {len(tombs)} tombstone(s) from Master Archive now? [y/N]: ").strip().lower()
                                if ans_clear in ("y", "yes"):
                                    data["tombstones"] = {}
                                    save_tombstones(data, master_dir)
                                    print(f"\n✔ Cleared and retired {len(tombs)} active tombstone(s) from Master Archive.\n")
                                else:
                                    print(">>> Kept tombstones in registry.\n")
                            except (KeyboardInterrupt, EOFError):
                                print("\n>>> Kept tombstones in registry.\n")
                        else:
                            print("\n>>> No pending files or tombstones found requiring purge.\n")
            elif choice.startswith("u ") or choice.startswith("unflag "):
                target_str = choice.split(maxsplit=1)[1].strip()
                selected_indices = parse_selection_indices(target_str, len(items))
                if not selected_indices:
                    print("Invalid selection.\n")
                    continue
                selected_keys = [items[idx - 1][0] for idx in selected_indices]
                master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
                data = load_tombstones(master_dir)
                tombs = data.get("tombstones", {})
                unflagged_count = 0
                for k in selected_keys:
                    if k in tombs:
                        del tombs[k]
                        unflagged_count += 1
                save_tombstones(data, master_dir)
                print(f"\n✔ Unflagged {unflagged_count} package(s).\n")
                continue
            else:
                print("Invalid choice. Enter [u <#>] to unflag, [P] to purge, or [b] to go back.\n")
        except (KeyboardInterrupt, EOFError):
            break


def display_package_sei_breakdown(packages_or_items, analyzer=None):
    """
    Lists files grouped by timestamp prefix for all cameras (front, back, repeaters, pillars, cabin)
    and checks SEI metadata status for each individual camera file.

    For each timestamp prefix, displays:
      a) ✔ (All N have SEI) [with sample telemetry: speed, gear, AP]
      b) ❌ (0/N have SEI)
      c) ✔ x X  ❌ x Y (partial SEI presence)
    """
    if not packages_or_items:
        print("\n⚠️  No footage provided to inspect.\n")
        return

    pkgs = packages_or_items if isinstance(packages_or_items, list) else [packages_or_items]

    normalized_pkgs = []
    for item in pkgs:
        if isinstance(item, dict) and "files" in item:
            normalized_pkgs.append(item)
        elif isinstance(item, dict) and "path" in item:
            normalized_pkgs.append({"files": [item["path"]], "cat": item.get("cat", "Footage"), "folder": os.path.dirname(item["path"])})
        elif isinstance(item, str) and os.path.isfile(item):
            normalized_pkgs.append({"files": [item], "cat": "Footage", "folder": os.path.dirname(item)})

    if not normalized_pkgs:
        print("\n⚠️  No footage files found to inspect.\n")
        return

    print("\n" + "=" * 80)
    print(" 🔍 TESLACAM FILES & SEI METADATA BREAKDOWN")
    print("=" * 80)

    total_all_files = 0
    total_all_sei = 0
    total_all_no_sei = 0
    total_all_enc = 0

    for idx, pkg in enumerate(normalized_pkgs, 1):
        files = pkg.get("files", [])
        if not files:
            continue

        cat = pkg.get("cat", "Footage")
        icon = "🔄" if cat == "Recent" else ("💾" if cat == "Saved" else ("🔴" if cat == "Sentry" else "📁"))
        folder = pkg.get("folder") or (os.path.dirname(files[0]) if files else "TeslaCam")
        rel_p = pkg.get("rel_path") or os.path.basename(folder)
        reason = pkg.get("reason", "")
        reason_str = f" — {reason}" if reason else ""

        print(f"\n  📂 [{icon} {cat}Clips] {folder}{reason_str}")

        # Group files by timestamp prefix (YYYY-MM-DD_HH-MM-SS or YYYY-MM-DD_HH-MM)
        groups = defaultdict(list)
        for f in files:
            bn = os.path.basename(f)
            m = re.match(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", bn)
            if not m:
                m = re.match(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})", bn)
            prefix = m.group(1) if m else os.path.splitext(bn)[0]
            groups[prefix].append(f)

        pkg_files_cnt = 0
        pkg_sei_cnt = 0
        pkg_no_sei_cnt = 0
        pkg_enc_cnt = 0

        for prefix in sorted(groups.keys()):
            cam_files = sorted(groups[prefix])
            num_cams = len(cam_files)
            pkg_files_cnt += num_cams

            # Format time representation
            time_part = prefix
            m_t = re.search(r"(\d{2})-(\d{2})-(\d{2})$", prefix)
            if m_t:
                time_part = f"{m_t.group(1)}:{m_t.group(2)}:{m_t.group(3)}"
            else:
                m_t2 = re.search(r"(\d{2})-(\d{2})$", prefix)
                if m_t2:
                    time_part = f"{m_t2.group(1)}:{m_t2.group(2)}"

            # Inspect each camera file
            sei_files = []
            no_sei_files = []
            enc_files = []
            sample_telem = None

            for cf in cam_files:
                st = inspect_mp4_clip(cf)
                if st.get("is_encrypted"):
                    enc_files.append((cf, st))
                elif st.get("has_sei"):
                    sei_files.append((cf, st))
                    if not sample_telem and st.get("telemetry_sample"):
                        sample_telem = st.get("telemetry_sample")
                else:
                    no_sei_files.append((cf, st))

            n_sei = len(sei_files)
            n_no_sei = len(no_sei_files)
            n_enc = len(enc_files)

            pkg_sei_cnt += n_sei
            pkg_no_sei_cnt += n_no_sei
            pkg_enc_cnt += n_enc

            # Determine mark
            if n_enc == num_cams:
                mark = f"🔒 (All {num_cams} encrypted)"
            elif n_sei == num_cams:
                telem_bits = []
                if sample_telem:
                    if "speed_kmh" in sample_telem:
                        telem_bits.append(f"{sample_telem['speed_kmh']} km/h")
                    if "gear" in sample_telem:
                        telem_bits.append(f"Gear {sample_telem['gear']}")
                    if "autopilot_state" in sample_telem:
                        telem_bits.append(f"AP: {sample_telem['autopilot_state']}")
                telem_str = f": {', '.join(telem_bits)}" if telem_bits else ""
                mark = f"✔ (All {num_cams} have SEI{telem_str})"
            elif n_sei == 0 and n_enc == 0:
                mark = f"❌ (0/{num_cams} have SEI)"
            else:
                parts = []
                if n_sei > 0:
                    parts.append(f"✔ x {n_sei}")
                if n_no_sei > 0:
                    parts.append(f"❌ x {n_no_sei}")
                if n_enc > 0:
                    parts.append(f"🔒 x {n_enc}")
                mark = "  ".join(parts)

            cams_label = f"[{num_cams} file{'s' if num_cams != 1 else ''}]"
            print(f"     • {time_part} ➔ {prefix}-*.mp4  {cams_label:<10} {mark}")

        total_all_files += pkg_files_cnt
        total_all_sei += pkg_sei_cnt
        total_all_no_sei += pkg_no_sei_cnt
        total_all_enc += pkg_enc_cnt

        summary_parts = []
        if pkg_sei_cnt > 0:
            summary_parts.append(f"✔ {pkg_sei_cnt} with SEI")
        if pkg_no_sei_cnt > 0:
            summary_parts.append(f"❌ {pkg_no_sei_cnt} without SEI")
        if pkg_enc_cnt > 0:
            summary_parts.append(f"🔒 {pkg_enc_cnt} encrypted")
        res_str = ", ".join(summary_parts) if summary_parts else "No video files"
        print(f"     └─ Package Summary: {len(groups)} interval(s), {pkg_files_cnt} files ({res_str})")

    print("\n" + "-" * 80)
    grand_parts = []
    if total_all_sei > 0:
        grand_parts.append(f"✔ {total_all_sei} with SEI")
    if total_all_no_sei > 0:
        grand_parts.append(f"❌ {total_all_no_sei} without SEI")
    if total_all_enc > 0:
        grand_parts.append(f"🔒 {total_all_enc} encrypted")
    grand_str = ", ".join(grand_parts) if grand_parts else "None"
    print(f" Total Checked: {total_all_files} video files across inspected package(s) ({grand_str})")
    print("=" * 80 + "\n")


def run_interactive_footage_review(packages, analyzer, context_label="Event Footage"):
    """
    Interactive Step-Through Review Wizard:
    Sequentially reviews footage packages, launches macOS Quick Look video preview for each,
    and prompts user to Keep ([k] / Enter), Copy ([c]), Delete ([f]), or Open ([o]).
    """
    if not packages:
        print("\n⚠️  No footage packages available to review.")
        return 0, 0, 0

    total_pkgs = len(packages)
    print("\n" + "=" * 76)
    print(f" 🎬 INTERACTIVE FOOTAGE REVIEW WIZARD: {context_label}")
    print("=" * 76)
    print(f" Stepping through {total_pkgs} footage package(s).")
    print(" Footage clips will be revealed in macOS Finder.")
    print(" Commands:")
    for _cmd in ("[k]eep / next (Enter)", "[c]opy locally", "[f]lag for deletion", "[u]nflag",
                 "[v]iew cameras", "[o]pen", "[s]kip", "[q]uit"):
        print(f"   {_cmd}")
    print("=" * 76)

    kept_count = 0
    deleted_count = 0
    freed_bytes_total = 0
    tombstones_total = 0
    current_proc = None

    try:
        for idx, pkg in enumerate(packages, 1):
            close_preview_process(current_proc)
            current_proc = None

            primary_clip = get_primary_preview_clip(pkg)
            cam_label = "Front Camera"
            if primary_clip:
                if "-back.mp4" in primary_clip:
                    cam_label = "Back Camera"
                elif "-left_repeater.mp4" in primary_clip:
                    cam_label = "Left Repeater"
                elif "-right_repeater.mp4" in primary_clip:
                    cam_label = "Right Repeater"

            icon = "🔄" if pkg["cat"] == "Recent" else ("💾" if pkg["cat"] == "Saved" else ("🔴" if pkg["cat"] == "Sentry" else "📁"))
            mb_val = pkg["tot_size"] / (1024 * 1024)
            sz_str = f"{mb_val/1024:.1f} GB" if mb_val >= 1024 else f"{mb_val:.0f} MB"

            dest_p, local_primary = open_footage_package(pkg, analyzer, reveal_clip=True, ask_preference=True)
            if local_primary:
                primary_clip = local_primary

            box_w = 78
            p_rel = pkg["rel_path"]
            p_cnt = pkg["files_count"]
            n_drives = max(1, len(analyzer.teslacam_dirs))
            flagged_already = is_package_flagged(pkg, repo_root=analyzer.repo_root)
            status_flag = " 🗑️  [FLAGGED FOR DELETION]" if flagged_already else ""

            time_range_disp = pkg.get("time_range_str") or pkg["time_str"]
            event_idx = pkg.get("event_idx")
            ev_act = pkg.get("event_activity")
            ev_time = pkg.get("event_time_str")

            print(f"\n┌{'─'*box_w}┐")
            hdr = f" [{idx}/{total_pkgs}] {icon} {pkg['cat']} Footage: {pkg['reason']}{status_flag}"
            print(f"│{pad_display(hdr, box_w, 'left')}│")
            print(f"├{'─'*box_w}┤")
            print(f"│{pad_display(f' Time Range : {time_range_disp}', box_w, 'left')}│")
            if event_idx is not None and ev_act:
                ev_str = f"[#{event_idx}] {ev_act}"
                if ev_time and ev_time not in ev_act:
                    ev_str = f"[#{event_idx}] ({ev_time}) {ev_act}"
                ev_lines = wrap_text_display(ev_str.strip(), box_w - 15)
                for i_ev, ev_l in enumerate(ev_lines):
                    pfx = " Event      : " if i_ev == 0 else "              "
                    print(f"│{pad_display(f'{pfx}{ev_l}', box_w, 'left')}│")
            print(f"│{pad_display(f' Package    : {p_rel}', box_w, 'left')}│")
            print(f"│{pad_display(f' Files      : {p_cnt} files ({sz_str}) across {n_drives} mounted drive(s)', box_w, 'left')}│")
            if primary_clip:
                status_prev = f" Highlight  : {os.path.basename(primary_clip)} ({cam_label})"
            else:
                status_prev = " Highlight  : ⚠️  No previewable MP4 file found"
            print(f"│{pad_display(status_prev, box_w, 'left')}│")

            if primary_clip and os.path.exists(primary_clip):
                st_info = inspect_mp4_clip(primary_clip)
                if st_info.get("is_encrypted"):
                    enc_scheme = st_info.get("encryption_scheme") or "CENC"
                    print(f"│{pad_display(f' Stream     : 🔒 Encrypted ({enc_scheme})', box_w, 'left')}│")
                elif st_info.get("has_sei"):
                    sample = st_info.get("telemetry_sample", {})
                    telem_bits = []
                    if "speed_kmh" in sample:
                        telem_bits.append(f"{sample['speed_kmh']} km/h")
                    if "gear" in sample:
                        telem_bits.append(f"Gear {sample['gear']}")
                    if "autopilot_state" in sample:
                        telem_bits.append(f"AP: {sample['autopilot_state']}")
                    telem_str = ", ".join(telem_bits) if telem_bits else "Available"
                    print(f"│{pad_display(f' Telemetry  : 📊 SEI ({telem_str})', box_w, 'left')}│")
                else:
                    codec = st_info.get("codec", "H.264")
                    print(f"│{pad_display(f' Stream     : {codec} (Standard video, No SEI)', box_w, 'left')}│")
            print(f"└{'─'*box_w}┘")

            while True:
                print("Action:")
                print("  [k]eep / next (Enter)")
                print("  [c]opy locally")
                print("  [f]lag delete")
                print("  [u]nflag")
                print("  [v]iew cams")
                print("  [o]pen")
                print("  [s]kip")
                print("  [q]uit")
                try:
                    ans = input("> ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    ans = "q"

                if ans in ("", "k", "keep", "n", "next"):
                    kept_count += 1
                    break
                elif ans in ("c", "copy"):
                    date_val = pkg.get("date_str") or (pkg["start_dt"].strftime("%Y-%m-%d") if pkg.get("start_dt") else None)
                    copy_clips_locally(pkg.get("files", []), analyzer, date_or_str=date_val, ask_preference=True)
                    continue
                elif ans in ("f", "flag", "d", "del", "delete"):
                    n_flag, b_flag = flag_footage_packages_for_deletion([pkg], analyzer)
                    deleted_count += 1
                    freed_bytes_total += b_flag
                    tombstones_total += n_flag
                    flag_mb = b_flag / (1024 * 1024)
                    flag_sz = f"{flag_mb/1024:.1f} GB" if flag_mb >= 1024 else f"{flag_mb:.0f} MB"
                    ev_ref = f" (Event #{pkg['event_idx']})" if pkg.get("event_idx") else ""
                    print(f" 🗑️  Flagged for deletion: {pkg['rel_path']} [{time_range_disp}]{ev_ref} (~{flag_sz} reclaimable on next sync).\n")
                    break
                elif ans in ("u", "unflag"):
                    unflag_footage_packages([pkg], analyzer)
                    print(f" ✔ Removed deletion flag from {pkg['rel_path']}.\n")
                    break
                elif ans in ("v", "view", "play"):
                    mp4s = [f for f in pkg.get("files", []) if f.endswith(".mp4") and os.path.isfile(f)]
                    if not mp4s:
                        print(" ⚠️  No MP4 video files in this package.")
                        continue
                    print("\n Available Camera Angles:")
                    for c_i, c_f in enumerate(mp4s, 1):
                        print(f"   [{c_i}] {os.path.basename(c_f)}")
                    print("   [a] Open all cameras in Quick Look")
                    print("   [Enter] Return to review")
                    try:
                        c_pick = input(" Select camera angle [1-N or a]: ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        c_pick = ""
                    if c_pick == "a":
                        close_preview_process(current_proc)
                        current_proc = None
                        for c_f in mp4s:
                            open_footage_package(pkg, analyzer, chosen_file=c_f, reveal_clip=True, ask_preference=False)
                        print(" 📹 Launched all camera angles in Quick Look.\n")
                    elif c_pick.isdigit() and 1 <= int(c_pick) <= len(mp4s):
                        close_preview_process(current_proc)
                        chosen_f = mp4s[int(c_pick) - 1]
                        open_footage_package(pkg, analyzer, chosen_file=chosen_f, reveal_clip=True, ask_preference=False)
                        print(f" 📹 Launched: {os.path.basename(chosen_f)}\n")
                    continue
                elif ans in ("o", "open"):
                    open_footage_package(pkg, analyzer, reveal_clip=False, ask_preference=True)
                    continue
                elif ans in ("s", "skip"):
                    print("\n Skipping remainder of review.")
                    break
                elif ans in ("q", "quit", "exit"):
                    close_preview_process(current_proc)
                    print("\n Exiting review wizard.")
                    return kept_count, deleted_count, freed_bytes_total
                else:
                    print(" Invalid option. Press Enter to keep, 'c' to copy, 'f' to delete, 'v' to switch camera angle, or 'q' to quit.")

            if ans in ("s", "skip"):
                break

    finally:
        close_preview_process(current_proc)

    tot_freed_mb = freed_bytes_total / (1024 * 1024)
    tot_sz_str = f"{tot_freed_mb/1024:.1f} GB" if tot_freed_mb >= 1024 else f"{tot_freed_mb:.0f} MB"
    print("\n" + "=" * 76)
    print(" 🎬 REVIEW WIZARD SUMMARY")
    print("=" * 76)
    print(f" Packages Kept   : {kept_count}")
    print(f" Packages Deleted: {deleted_count} ({tot_sz_str} reclaimed across mounted drives)")
    if tombstones_total > 0:
        print(f" Tombstones Added: {tombstones_total} (saved to Tessie/dashcam_tombstones.json)")
    print("=" * 76 + "\n")

    return kept_count, deleted_count, freed_bytes_total


def render_footage_listing(clips, indent=""):
    by_cat = defaultdict(list)
    for c in clips:
        by_cat[c['cat']].append(c)
        
    for cat in ['Recent', 'Saved', 'Sentry', 'Other']:
        if cat not in by_cat:
            continue
        cat_clips = by_cat[cat]
        icon = '🔄' if cat == 'Recent' else ('💾' if cat == 'Saved' else ('🔴' if cat == 'Sentry' else '📁'))
        
        if cat == 'Recent':
            folder_path = os.path.dirname(cat_clips[0]['path'])
            print(f"\n{indent}📂 [{icon} RecentClips] {folder_path}")
            by_ts = sorted(list(set(c['dt'] for c in cat_clips)))
            for dt in by_ts:
                ts_str = dt.strftime('%H:%M:%S')
                f_pattern = dt.strftime('%Y-%m-%d_%H-%M-%S-*.mp4')
                print(f"{indent}   • {ts_str} ➔ {f_pattern}")
        else:
            base_dir = os.path.dirname(os.path.dirname(cat_clips[0]['path']))
            folder_cat_name = f"{cat}Clips"
            print(f"\n{indent}📂 [{icon} {folder_cat_name}] {base_dir}")
            
            by_folder = defaultdict(list)
            for c in cat_clips:
                by_folder[c['folder']].append(c)
                
            for folder, f_clips in sorted(by_folder.items()):
                folder_name = os.path.basename(folder)
                ev_json = os.path.join(folder, 'event.json')
                reason = 'event'
                event_ts_str = None
                if os.path.exists(ev_json):
                    try:
                        with open(ev_json) as fp:
                            data = json.load(fp)
                            reason = clean_event_reason(data.get('reason', 'event'))
                            if 'timestamp' in data:
                                event_ts_str = data['timestamp'].split('T')[-1]
                    except Exception:
                        pass
                
                unique_dts = sorted(list(set(c['dt'] for c in f_clips)))
                num_clips = len(unique_dts)
                dur_m = num_clips
                
                if not event_ts_str:
                    event_ts_str = unique_dts[0].strftime('%H:%M:%S') if unique_dts else folder_name.split('_')[-1].replace('-', ':')
                
                date_prefix = unique_dts[0].strftime('%Y-%m-%d') if unique_dts else folder_name.split('_')[0]
                events_word = "event" if num_clips == 1 else "events"
                print(f"{indent}   • {event_ts_str} ({reason}) ➔ {folder_name}/{date_prefix}_*.mp4 ({num_clips} {events_word}, {dur_m}m)")


def display_footage_details(trip, analyzer):
    """Level 3: Deep listing of exact video files by camera angle for a single drive."""
    t_start = trip["start_dt"]
    t_end = trip["end_dt"]
    
 # Continuous footage across prior window (5m), full drive, and post-drive window (5m)
    all_clips = analyzer.find_footage_range(t_start, t_end, prior_seconds=300, after_seconds=300)
    
    entry_clips = [c for c in all_clips if c["dt"] < t_start]
    drive_clips = [c for c in all_clips if t_start <= c["dt"] <= t_end]
    exit_clips = [c for c in all_clips if c["dt"] > t_end]
    
    box_inner = 72
    print()
    print(format_box_line("┌", "─", "┐", [box_inner]))
    print(format_title_line(f" 📹 CAMERA FOOTAGE: {t_start.strftime('%a %d %b %Y')} ({t_start.strftime('%H:%M')} ➔ {t_end.strftime('%H:%M')})", box_inner))
    print(format_box_line("├", "─", "┤", [box_inner]))
    if trip['start_place'] == trip['end_place']:
        print(format_title_line(f"    Location    : 🅿️ {trip['start_place']} ({trip['start_addr'].split(',')[0]})", box_inner))
    else:
        print(format_title_line(f"    Origin      : {trip['start_place']} ({trip['start_addr'].split(',')[0]})", box_inner))
        print(format_title_line(f"    Destination : {trip['end_place']} ({trip['end_addr'].split(',')[0]})", box_inner))
    print(format_box_line("└", "─", "┘", [box_inner]))
    
    sec_idx = 1
    if entry_clips:
        print(f"\n🚪 {sec_idx}. PRE-DRIVE & ENTRY WINDOW (Prior to ~{t_start.strftime('%H:%M:%S')}):")
        render_footage_listing(entry_clips, indent="  ")
        sec_idx += 1

    if drive_clips:
        print(f"\n🚗 {sec_idx}. IN-DRIVE FOOTAGE ({t_start.strftime('%H:%M:%S')} ➔ {t_end.strftime('%H:%M:%S')}):")
        render_footage_listing(drive_clips, indent="  ")
        sec_idx += 1

    if exit_clips:
        print(f"\n🚪 {sec_idx}. POST-DRIVE & EXIT WINDOW (Arrival ~{t_end.strftime('%H:%M:%S')} + Post Context):")
        render_footage_listing(exit_clips, indent="  ")
        sec_idx += 1

    if not all_clips:
        print(f"\n🚪 1. TRIP FOOTAGE WINDOW ({t_start.strftime('%H:%M:%S')} ➔ {t_end.strftime('%H:%M:%S')}):")
        print("   ⚠️ No local footage found on archive drives.")
        sec_idx += 1

 # Drive deep-dive telemetry (AGENTS.md TODO-019) - shown automatically
 # alongside footage whenever it exists, no separate footage-vs-deep-dive
 # prompt, mirroring how analyse_charging.py's inspect_session()
 # already shows its charging curve inline.
    print(f"\n📊 {sec_idx}. DRIVE TELEMETRY:")
    dd_chart_path = None
    deepdive_result = analyzer.get_drive_deepdive(trip)
    if not deepdive_result:
        print("   ℹ️  No deep-dive telemetry (drive_deepdive_*.csv) found for this drive.")
    else:
        dd_rec, dd_segments, dd_chart_path = deepdive_result
        print(f"   Deep-Dive File: {dd_rec.get('source_file', '')} ({dd_rec.get('samples_count', 0)} samples)")
        cls_icon = {"climbing": "⬆️ ", "descending": "⬇️ ", "level": "➡️ ", "stationary": "⏸ "}
        for seg in dd_segments:
            icon = cls_icon.get(seg["class"], "  ")
            eff = seg["efficiency_kwh_per_100km"]
            eff_str = f"{eff:.1f} kWh/100km" if eff is not None else "-"
            print(f"     {icon}{seg['class']:<11} {seg['distance_km']:5.2f} km  {seg['duration_mins']:5.1f} min  "
                  f"Δelev {seg['elevation_change_m']:+6.1f} m   eff: {eff_str}")
        if dd_chart_path:
            print(f"   Elevation/Efficiency Profile: {dd_chart_path}")

 # 4. Comparative ICE Fuel Running Costs
    dist_km = float(trip.get("dist_km", 0))
    start_dt = trip.get("start_dt")
    dur_mins = (trip["end_dt"] - trip["start_dt"]).total_seconds() / 60.0 if (trip.get("end_dt") and trip.get("start_dt")) else None
    raw_samples = dd_rec.get("raw_samples") if deepdive_result else None
    try:
        kwh = float(trip.get("kwh_used")) if trip.get("kwh_used") else None
    except (ValueError, TypeError):
        kwh = None

    td_home = analyzer.tessie_data_home or (analyzer.tessie_dirs[0] if analyzer.tessie_dirs else None)
    fuel_lines = format_drive_fuel_comparison(
        dist_km, start_dt, kwh_used=kwh,
        duration_mins=dur_mins, raw_samples=raw_samples,
        tessie_dir=td_home, indent="   "
    )
    if fuel_lines:
        print()
        for fl in fuel_lines:
            print(fl)

    print(f"\n💡 Quick Tips:")
    print(f"   • To view side doors (getting in/out): check *-left_repeater.mp4 or *-right_repeater.mp4")
    print(f"   • To view trunk/rear walking: check *-back.mp4")
    has_chart = bool(dd_chart_path and os.path.exists(dd_chart_path))
    has_footage = bool(all_clips)

    recent_clips = [c for c in all_clips if c.get("cat") == "Recent"]
    sentry_clips = [c for c in all_clips if c.get("cat") == "Sentry"]
    saved_clips = [c for c in all_clips if c.get("cat") == "Saved"]

    has_recent = bool(recent_clips)
    has_sentry = bool(sentry_clips)
    has_saved = bool(saved_clips)

    if has_recent:
        print(f"   • RecentClips: use [r]ecent action below to copy locally into organized folder")
    if has_sentry:
        s_folder = sentry_clips[0].get("folder") or os.path.dirname(sentry_clips[0]["path"])
        print(f"   • Open SentryClips in Finder: open \"{s_folder}\"")
    if has_saved:
        v_folder = saved_clips[0].get("folder") or os.path.dirname(saved_clips[0]["path"])
        print(f"   • Open SavedClips in Finder: open \"{v_folder}\"")

    if has_footage:
        sample_path = all_clips[0].get("path") if all_clips else None
        if sample_path and os.path.exists(sample_path):
            st_info = inspect_mp4_clip(sample_path)
            st_banner = format_stream_banner(st_info)
            print(f"\n{st_banner}")
            if st_info.get("is_encrypted"):
                print(f"   ⚠️  Stream is encrypted ({st_info.get('encryption_scheme', 'CENC')}) — requires vehicle decryption tools to view.")

    if (has_footage or has_chart) and sys.stdin.isatty():
        while True:
            try:
                print("\nAction:")
                if has_footage:
                    print("  [c]   Copy all footage locally (<landing_directory>/TeslaCam/<YYYY-MM-DD>/)")
                if has_chart:
                    print("  [o]   Open elevation & efficiency chart in browser")
                if has_recent:
                    print("  [r]   Copy / open RecentClips only")
                if has_sentry:
                    print("  [s]   Copy / open SentryClips in Finder")
                if has_saved:
                    print("  [v]   Copy / open SavedClips in Finder")
                if has_footage:
                    print("  [l]   List files & inspect SEI metadata (by camera timestamp prefix)")
                if has_footage:
                    print("  [d]   Permanently delete all footage for this trip across archive drives")
                print("  [b]   Back / continue to trips list (or press Enter)")
                print("  [q]   Quit")
                action_choice = input("\nChoice: ").strip().lower()
                date_str = trip["start_dt"].strftime("%Y-%m-%d")
                if action_choice == "q":
                    sys.exit(0)
                elif action_choice in ("b", "back", ""):
                    break
                elif action_choice in ("l", "list") and has_footage:
                    pkgs = build_timeline_footage_packages({"clips": all_clips, "start_dt": trip["start_dt"], "end_dt": trip["end_dt"]})
                    display_package_sei_breakdown(pkgs, analyzer)
                    continue
                elif action_choice in ("d", "del", "delete") and has_footage:
                    pkgs = build_timeline_footage_packages({"clips": all_clips, "start_dt": trip["start_dt"], "end_dt": trip["end_dt"]})
                    if pkgs:
                        total_files = sum(p["files_count"] for p in pkgs)
                        num_drives = max(1, len(analyzer.teslacam_dirs))
                        total_bytes = sum(p["tot_size"] for p in pkgs) * num_drives
                        mb_val = total_bytes / (1024 * 1024)
                        sz_str = f"{mb_val/1024:.1f} GB" if mb_val >= 1024 else f"{mb_val:.0f} MB"
                        print(f"\n⚠️  Permanently delete all footage for this trip (~{total_files * num_drives} files, {sz_str}) across {num_drives} mounted drive(s)? [y/N]: ", end="")
                        conf = input().strip().lower()
                        if conf in ("y", "yes"):
                            n_del, b_del, n_tombs = delete_footage_packages_across_drives(pkgs, analyzer)
                            del_mb = b_del / (1024 * 1024)
                            del_sz = f"{del_mb/1024:.1f} GB" if del_mb >= 1024 else f"{del_mb:.0f} MB"
                            print(f"✔ Successfully deleted {n_del} file(s) ({del_sz}) and recorded {n_tombs} tombstone(s).\n")
                elif action_choice in ("o", "open", "chart") and has_chart:
                    try_open_in_browser(dd_chart_path)
                elif action_choice in ("c", "copy", "all") and has_footage:
                    copy_clips_locally(all_clips, analyzer, date_or_str=date_str, ask_preference=True)
                elif action_choice in ("r", "recent") and has_recent:
                    open_footage_package(recent_clips, analyzer, date_or_str=date_str, reveal_clip=True, ask_preference=True, force_copy=True)
                elif action_choice in ("s", "sentry") and has_sentry:
                    open_footage_package(sentry_clips, analyzer, date_or_str=date_str, reveal_clip=False, ask_preference=True, force_copy=True)
                elif action_choice in ("v", "saved", "save") and has_saved:
                    open_footage_package(saved_clips, analyzer, date_or_str=date_str, reveal_clip=False, ask_preference=True, force_copy=True)
                elif action_choice in ("f", "folder") and has_footage:
                    open_footage_package(recent_clips or sentry_clips or saved_clips or all_clips, analyzer, reveal_clip=False)
            except (KeyboardInterrupt, EOFError):
                break


def drill_down_day(day_str, day_trips, analyzer):
    """Level 2: Display drives for a selected day in a structured table and allow picking a trip for footage listing."""
    while True:
        dt_obj = datetime.strptime(day_str, "%Y-%m-%d")
        total_km = sum(t["dist_km"] for t in day_trips)
        total_mins = sum(t["dur_min"] for t in day_trips)
        hours, mins = divmod(total_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
        
 # Precompute rows to dynamically measure max column width
        rows_data = []
        for i, t in enumerate(day_trips):
            t_start = t["start_dt"].strftime("%H:%M")
            t_end = t["end_dt"].strftime("%H:%M")
            # TODO-010: a trip whose start and end resolve to genuinely
            # different real per-event timezones (e.g. an interstate
            # border crossing) is ambiguous as a bare "HH:MM -> HH:MM" -
            # show each end's own real abbreviation whenever they
            # actually differ, so the crossing is visible right in the
            # day table instead of silently assumed away. No visual
            # change for the common case (every trip so far has been
            # within a single zone).
            t_start_abbr = t.get("start_tz_abbr") or ""
            t_end_abbr = t.get("end_tz_abbr") or ""
            if t_start_abbr and t_end_abbr and t_start_abbr != t_end_abbr:
                t_start = f"{t_start} {t_start_abbr}"
                t_end = f"{t_end} {t_end_abbr}"
            time_w = f" {t_start} ➔ {t_end}"
            dur_str = f" {format_duration_short(t['dur_min'])}"
            dist_str = f" {t['dist_km']:.1f} km"
            
            raw = t.get("raw", {})
            s_soc = t.get("start_soc") or raw.get("Starting Battery (%)", "-")
            e_soc = t.get("end_soc") or raw.get("Ending Battery (%)", "-")
            soc_str = f" {s_soc}%➔{e_soc}%" if s_soc and s_soc != "-" else " -"
            
            s_p = analyzer.display_place(t.get("start_place") or "Start")
            e_p = analyzer.display_place(t.get("end_place") or "End")
            if s_p.lower() == "home":
                s_p = "🏠 Home"
            if e_p.lower() == "home":
                e_p = "🏠 Home"
            if s_p == e_p or s_p.lstrip("🏠 ") == e_p.lstrip("🏠 "):
                route_str = f" 🅿️ {s_p}"
            else:
                route_str = f" {s_p} ➔ {e_p}"
            
            park_str = " —"
            if i < len(day_trips) - 1:
                next_start = day_trips[i+1]["start_dt"]
                d_mins = int((next_start - t["end_dt"]).total_seconds() / 60)
                if d_mins >= 0:
                    park_str = f" {format_duration_short(d_mins)}"
            
            f_tag, f_cats = analyzer.get_trip_footage_summary(t)
            
            rows_data.append({
                "idx": f" [{i+1}]",
                "time": time_w,
                "dur": dur_str,
                "dist": dist_str,
                "soc": soc_str,
                "route": route_str,
                "eff": f" {format_drive_efficiency(t)}",
                "park": park_str,
                "sav": "📹" if "Saved" in f_cats else "·",
                "sen": "🛡️" if "Sentry" in f_cats else "·",
                "rec": "🕒" if "Recent" in f_cats else "·"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_time = max(len(" Time Window "), max((display_len(r["time"]) + 1 for r in rows_data), default=15))
        w_dur = max(len(" Dur "), max((display_len(r["dur"]) + 1 for r in rows_data), default=8))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=11))
        w_soc = max(len(" SoC % "), max((display_len(r["soc"]) + 1 for r in rows_data), default=10))
        w_route = max(len(" Route (Origin ➔ Destination) "), max((display_len(r["route"]) + 1 for r in rows_data), default=30))
        w_eff = max(len(" Avg Efficiency "), max((display_len(r["eff"]) + 1 for r in rows_data), default=17))
        w_park = max(len(" Parked Duration "), max((display_len(r["park"]) + 1 for r in rows_data), default=14))
        
        w_sav = 7
        w_sen = 8
        w_rec = 8
        w_foot_total = w_sav + w_sen + w_rec + 2  # 25

        left_widths = [w_idx, w_time, w_dur, w_dist, w_soc, w_route, w_eff, w_park]
        left_headers = [" #", " Time Window", " Dur", " Distance", " SoC %", " Route (Origin ➔ Destination)", " Avg Efficiency", " Parked Duration"]

        total_inner = sum(left_widths) + len(left_widths) + w_foot_total
        title = f" 📅 {dt_obj.strftime('%A, %d %B %Y')} — {len(day_trips)} Drives (Total: {time_str}, {total_km:.1f} km)"
        t_len = display_len(title)
        if t_len + 2 > total_inner:
            w_route += (t_len + 2 - total_inner)
            left_widths[5] = w_route
            total_inner = sum(left_widths) + len(left_widths) + w_foot_total

        border_top = "┌" + "─" * total_inner + "┐"
        border_top_header = "├" + "┬".join("─"*w for w in left_widths) + "┬" + "─"*w_foot_total + "┤"
        border_sub = "│" + "│".join(" "*w for w in left_widths) + "├" + "─"*w_sav + "┬" + "─"*w_sen + "┬" + "─"*w_rec + "┤"
        border_data = "├" + "┼".join("─"*w for w in left_widths) + "┼" + "─"*w_sav + "┼" + "─"*w_sen + "┼" + "─"*w_rec + "┤"
        border_bot = "└" + "┴".join("─"*w for w in left_widths) + "┴" + "─"*w_sav + "┴" + "─"*w_sen + "┴" + "─"*w_rec + "┘"
        
        print(f"\n{border_top}")
        print(format_title_line(title, total_inner, "left"))
        print(border_top_header)
        
        row1_cols = [pad_display(h, w) for h, w in zip(bold_headers(left_headers), left_widths)] + [pad_display(f"\033[1mFootage\033[0m", w_foot_total, "center")]  # TODO-015: bolded to match analyse_charging.py header convention
        print("│" + "│".join(row1_cols) + "│")
        print(border_sub)
        
        row2_cols = [" "*w for w in left_widths] + [
            pad_display("Saved", w_sav, "center"),
            pad_display("Sentry", w_sen, "center"),
            pad_display("Recent", w_rec, "center")
        ]
        print("│" + "│".join(row2_cols) + "│")
        print(border_data)
        
        all_widths = left_widths + [w_sav, w_sen, w_rec]
 # idx/dur/dist/soc/park are numbers (with units, or plain for idx) -> right-aligned;
 # time and route are free text -> left-aligned; footage icons stay centered.
        all_aligns = ["right", "left", "right", "right", "right", "left", "right", "right", "center", "center", "center"]
        for r in rows_data:
            row_cells = [
                r["idx"] + " ", r["time"], r["dur"] + " ", r["dist"] + " ", r["soc"] + " ", r["route"], r["eff"] + " ", r["park"] + " ",
                r["sav"], r["sen"], r["rec"]
            ]
            print(format_row(row_cells, all_widths, all_aligns))
            
        print(border_bot)
        try:
            print("\nAction:")
            print(f"  [1-{len(day_trips)}] Select Trip to inspect footage & telemetry")
            print("  [t]    Open 24-Hour Event Timeline")
            print("  [a]    View all trips footage")
            print("  [b]    Back to days list")
            print("  [q]    Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["t", "timeline"]:
                display_timeline(dt_obj, analyzer)
            elif choice in ["a", "all"]:
                for t in day_trips:
                    display_footage_details(t, analyzer)
            elif choice.isdigit() and 1 <= int(choice) <= len(day_trips):
                display_footage_details(day_trips[int(choice)-1], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break


def drill_down_all_days(sorted_days, days_dict, analyzer, title):
    """Level 2 (combined): flatten every day in the current view into one
    chronologically-ordered trip table with a Date column, so [a]ll at the
    days menu shows the whole period at a glance instead of chaining through
    each day's own interactive drill-down one at a time."""
    all_trips = [(d_str, t) for d_str in sorted_days for t in days_dict[d_str]]

    while True:
        total_km = sum(t["dist_km"] for _, t in all_trips)
        total_mins = sum(t["dur_min"] for _, t in all_trips)
        hours, mins = divmod(total_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"

        rows_data = []
        for i, (d_str, t) in enumerate(all_trips):
            dt_day = datetime.strptime(d_str, "%Y-%m-%d")
            date_str = f" {dt_day.strftime('%a %d %b')}"
            t_start = t["start_dt"].strftime("%H:%M")
            t_end = t["end_dt"].strftime("%H:%M")
            time_w = f" {t_start} ➔ {t_end}"
            dur_str = f" {format_duration_short(t['dur_min'])}"
            dist_str = f" {t['dist_km']:.1f} km"

            raw = t.get("raw", {})
            s_soc = t.get("start_soc") or raw.get("Starting Battery (%)", "-")
            e_soc = t.get("end_soc") or raw.get("Ending Battery (%)", "-")
            soc_str = f" {s_soc}%➔{e_soc}%" if s_soc and s_soc != "-" else " -"

            s_p = analyzer.display_place(t.get("start_place") or "Start")
            e_p = analyzer.display_place(t.get("end_place") or "End")
            if s_p.lower() == "home":
                s_p = "🏠 Home"
            if e_p.lower() == "home":
                e_p = "🏠 Home"
            if s_p == e_p or s_p.lstrip("🏠 ") == e_p.lstrip("🏠 "):
                route_str = f" 🅿️ {s_p}"
            else:
                route_str = f" {s_p} ➔ {e_p}"

 # Parked Duration is the gap to the next trip - only meaningful
 # within the same day (the list crosses day boundaries, newest
 # day first), so it's only computed when the next entry is
 # chronologically later on the same date.
            park_str = " —"
            if i < len(all_trips) - 1 and all_trips[i+1][0] == d_str:
                next_start = all_trips[i+1][1]["start_dt"]
                d_mins = int((next_start - t["end_dt"]).total_seconds() / 60)
                if d_mins >= 0:
                    park_str = f" {format_duration_short(d_mins)}"

            f_tag, f_cats = analyzer.get_trip_footage_summary(t)

            rows_data.append({
                "idx": f" [{i+1}]",
                "date": date_str,
                "time": time_w,
                "dur": dur_str,
                "dist": dist_str,
                "soc": soc_str,
                "route": route_str,
                "eff": f" {format_drive_efficiency(t)}",
                "park": park_str,
                "sav": "📹" if "Saved" in f_cats else "·",
                "sen": "🛡️" if "Sentry" in f_cats else "·",
                "rec": "🕒" if "Recent" in f_cats else "·"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_date = max(len(" Date "), max((display_len(r["date"]) + 1 for r in rows_data), default=13))
        w_time = max(len(" Time Window "), max((display_len(r["time"]) + 1 for r in rows_data), default=15))
        w_dur = max(len(" Dur "), max((display_len(r["dur"]) + 1 for r in rows_data), default=8))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=11))
        w_soc = max(len(" SoC % "), max((display_len(r["soc"]) + 1 for r in rows_data), default=10))
        w_route = max(len(" Route (Origin ➔ Destination) "), max((display_len(r["route"]) + 1 for r in rows_data), default=30))
        w_eff = max(len(" Avg Efficiency "), max((display_len(r["eff"]) + 1 for r in rows_data), default=17))
        w_park = max(len(" Parked Duration "), max((display_len(r["park"]) + 1 for r in rows_data), default=14))

        w_sav = 7
        w_sen = 8
        w_rec = 8
        w_foot_total = w_sav + w_sen + w_rec + 2  # 25

        left_widths = [w_idx, w_date, w_time, w_dur, w_dist, w_soc, w_route, w_eff, w_park]
        left_headers = [" #", " Date", " Time Window", " Dur", " Distance", " SoC %", " Route (Origin ➔ Destination)", " Avg Efficiency", " Parked Duration"]

        total_inner = sum(left_widths) + len(left_widths) + w_foot_total
        title_str = f" 📋 {title} — All {len(all_trips)} Drives (Total: {time_str}, {total_km:.1f} km)"
        t_len = display_len(title_str)
        if t_len + 2 > total_inner:
            w_route += (t_len + 2 - total_inner)
            left_widths[6] = w_route
            total_inner = sum(left_widths) + len(left_widths) + w_foot_total

        border_top = "┌" + "─" * total_inner + "┐"
        border_top_header = "├" + "┬".join("─"*w for w in left_widths) + "┬" + "─"*w_foot_total + "┤"
        border_sub = "│" + "│".join(" "*w for w in left_widths) + "├" + "─"*w_sav + "┬" + "─"*w_sen + "┬" + "─"*w_rec + "┤"
        border_data = "├" + "┼".join("─"*w for w in left_widths) + "┼" + "─"*w_sav + "┼" + "─"*w_sen + "┼" + "─"*w_rec + "┤"
        border_bot = "└" + "┴".join("─"*w for w in left_widths) + "┴" + "─"*w_sav + "┴" + "─"*w_sen + "┴" + "─"*w_rec + "┘"

        print(f"\n{border_top}")
        print(format_title_line(title_str, total_inner, "left"))
        print(border_top_header)

        row1_cols = [pad_display(h, w) for h, w in zip(bold_headers(left_headers), left_widths)] + [pad_display(f"\033[1mFootage\033[0m", w_foot_total, "center")]  # TODO-015: bolded to match analyse_charging.py header convention
        print("│" + "│".join(row1_cols) + "│")
        print(border_sub)

        row2_cols = [" "*w for w in left_widths] + [
            pad_display("Saved", w_sav, "center"),
            pad_display("Sentry", w_sen, "center"),
            pad_display("Recent", w_rec, "center")
        ]
        print("│" + "│".join(row2_cols) + "│")
        print(border_data)

        all_widths = left_widths + [w_sav, w_sen, w_rec]
 # idx/dur/dist/soc are numbers (with units, or plain for idx) -> right-aligned;
 # date/time/route are free text -> left-aligned; footage icons stay centered.
        all_aligns = ["right", "left", "left", "right", "right", "right", "left", "right", "right", "center", "center", "center"]
        for r in rows_data:
            row_cells = [
                r["idx"] + " ", r["date"], r["time"], r["dur"] + " ", r["dist"] + " ", r["soc"] + " ", r["route"], r["eff"] + " ", r["park"] + " ",
                r["sav"], r["sen"], r["rec"]
            ]
            print(format_row(row_cells, all_widths, all_aligns))

        print(border_bot)
        try:
            print("\nAction:")
            print(f"  [1-{len(all_trips)}] Select Trip to inspect footage & telemetry")
            print("  [a]    View all trips footage")
            print("  [b]    Back to days list")
            print("  [q]    Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["a", "all"]:
                for _, t in all_trips:
                    display_footage_details(t, analyzer)
            elif choice.isdigit() and 1 <= int(choice) <= len(all_trips):
                display_footage_details(all_trips[int(choice)-1][1], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break


def parse_selection_indices(input_str, max_idx):
    """Parses selections like '1,5,8', '6-9', '3', 'all', 'a' into a sorted list of unique 1-based indices."""
    if not input_str:
        return []
    s = input_str.strip().lower()
    if s in ("all", "a", "*"):
        return list(range(1, max_idx + 1))
    
    selected = set()
    parts = s.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].strip().isdigit() and sub[1].strip().isdigit():
                start_i = int(sub[0].strip())
                end_i = int(sub[1].strip())
                if start_i > end_i:
                    start_i, end_i = end_i, start_i
                for idx in range(start_i, end_i + 1):
                    if 1 <= idx <= max_idx:
                        selected.add(idx)
        elif part.isdigit():
            idx = int(part)
            if 1 <= idx <= max_idx:
                selected.add(idx)
    return sorted(list(selected))


def make_event_slug(ev):
    """Generates a clean, filesystem-safe folder name for exporting an event's footage."""
    dt_str = ev["start_dt"].strftime("%Y-%m-%d_%H-%M")
    if ev.get("type") == "drive" and ev.get("drive"):
        d = ev["drive"]
        raw = f"{dt_str}_Drive_{d['start_place']}_to_{d['end_place']}"
    else:
        loc = ev.get("location", "Parked")
        raw = f"{dt_str}_Parked_{loc}"
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug


def render_timeline_footage_table(event, analyzer):
    """Renders a structured Unicode box table of footage packages for an event."""
    packages = build_timeline_footage_packages(event)
    ensure_package_sizes(packages)
    w_idx = 5
    w_time = 15
    w_type = 11
    w_reason = 25
    w_files = 21
    w_path = 44
    col_widths = [w_idx, w_time, w_type, w_reason, w_files, w_path]
    base_inner = sum(col_widths) + len(col_widths) - 1
    
    t_start = event["start_dt"]
    title = f" 📹 Event Footage: {t_start.strftime('%a %d %b %Y')} ({event['time_str']}) — {event['activity']}"
    
    t_len = display_len(title)
    if t_len >= base_inner:
        w_path += (t_len - base_inner) + 3
        col_widths = [w_idx, w_time, w_type, w_reason, w_files, w_path]
    total_inner = sum(col_widths) + len(col_widths) - 1
    
    if not packages:
        print(f"┌{'─'*total_inner}┐")
        print(f"│{pad_display(title, total_inner, 'left')}│")
        print(f"├{'─'*total_inner}┤")
        print(f"│{pad_display(' ⚠️  No local camera footage found on archive drives for this event.', total_inner, 'left')}│")
        print(f"└{'─'*total_inner}┘")
        return packages

    h_row = [f" {'#':^3} ", " Time / Window", " Type", " Trigger / Reason", " Cameras & Files", " TeslaCam Path / Target"]

    print(format_box_line("┌", "─", "┐", [total_inner]))
    print(f"│{pad_display(title, total_inner, 'left')}│")
    print(format_box_line("├", "┬", "┤", col_widths))
    print(format_row(h_row, col_widths, aligns=["center", "left", "left", "left", "left", "left"]))
    print(format_box_line("├", "┼", "┤", col_widths))

    total_files = sum(p["files_count"] for p in packages)
    total_bytes = sum(p["tot_size"] for p in packages)
    total_mb = total_bytes / (1024 * 1024)
    total_size_str = f"{total_mb/1024:.1f} GB" if total_mb >= 1024 else f"{total_mb:.0f} MB"

    tombstones_data = load_tombstones(analyzer.repo_root)

    for p in packages:
        is_fl = is_package_flagged(p, tombstones_data=tombstones_data, repo_root=analyzer.repo_root)
        if is_fl:
            type_label = "🗑️ Flagged"
        else:
            icon = "🔄" if p["cat"] == "Recent" else ("💾" if p["cat"] == "Saved" else ("🔴" if p["cat"] == "Sentry" else "📁"))
            type_label = f"{icon} {p['cat']}"
        size_mb = p["tot_size"] / (1024 * 1024)
        size_str = f"{size_mb/1024:.1f} GB" if size_mb >= 1024 else f"{size_mb:.0f} MB"
        files_label = f"{p['files_count']} files ({size_str})"
        
        c_idx = f" [{p['pkg_idx']:>2}]"
        c_time = f" {p['time_str']}"
        c_type = f" {type_label}"
        c_reason = f" {p['reason']}"
        c_files = f" {files_label}"
        c_path = f" {p['rel_path']}"
        
        row_vals = [c_idx, c_time, c_type, c_reason, c_files, c_path]
        print(format_row(row_vals, col_widths, aligns=["left", "left", "left", "left", "left", "left"]))

    print(format_box_line("├", "┴", "┤", col_widths))
    sum_msg = f" Summary: {len(packages)} footage items ({total_files} video files, {total_size_str})"
    print(f"│{pad_display(sum_msg, total_inner, 'left')}│")
    print(format_box_line("└", "─", "┘", [total_inner]))
    return packages


def display_timeline_event_details(event, analyzer):
    """Level 3 for Timeline: Detailed listing & export menu for footage within a specific event."""
    while True:
        packages = render_timeline_footage_table(event, analyzer)
        if not packages:
            if not sys.stdin.isatty():
                break
            try:
                print("Navigation:")
                print("  [b]ack")
                print("  [q]uit")
                c = input("> ").strip().lower()
                if c == "q":
                    sys.exit(0)
                break
            except (KeyboardInterrupt, EOFError):
                break

        if not sys.stdin.isatty():
            break

        try:
            n_flagged = len(get_flagged_tombstones(analyzer.repo_root))
            print("\nAction:")
            print("  [1, 2, 'all'] Copy selected footage package(s) locally (<landing_directory>/TeslaCam/<YYYY-MM-DD>/)")
            print("  [v <#>]       Preview footage in macOS Quick Look (e.g. 'v 1', 'v 2')")
            print("  [l <#>]       List files & inspect SEI metadata (e.g. 'l 1', 'l 2-4', 'l all', or 'l')")
            print("  [f <#>]       Flag footage package for deletion (e.g. 'f 1', 'f 2-4', 'f all')")
            print("  [u <#>]       Unflag footage package (e.g. 'u 1', 'u 2-4', 'u all')")
            if n_flagged > 0:
                print(f"  [p]           Purge all flagged deletions ({n_flagged} staged across mounted drives)")
            print("  [r]           Review wizard (step through event footage sequentially)")
            print("  [o]           Open event folder(s) directly in Finder")
            print("  [b]           Back to timeline")
            print("  [q]           Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["r", "review"]:
                run_interactive_footage_review(packages, analyzer, context_label=f"Event {event['time_str']}")
                s_dt = event["start_dt"]
                e_dt = event.get("end_dt", s_dt + timedelta(seconds=120))
                event["clips"] = [c for c in analyzer.footage_db if s_dt <= c["dt"] < e_dt]
                continue
            elif choice in ["o", "open"]:
                for p in packages:
                    open_footage_package(p, analyzer, reveal_clip=False, ask_preference=True)
                continue
            elif choice in ["p", "purge"]:
                flagged_tombs = get_flagged_tombstones(analyzer.repo_root)
                if not flagged_tombs:
                    print("\n ℹ️  No footage packages are currently flagged for deletion.\n")
                    continue
                display_flagged_deletions_table(analyzer)
                try:
                    conf = input(" Type 'yes' or 'y' to permanently purge all flagged footage now across mounted drives: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    print("\n Purge cancelled.\n")
                    continue
                if conf in ("y", "yes"):
                    n_del, b_del, n_tombs = purge_flagged_tombstones_across_drives(analyzer.teslacam_dirs, analyzer.repo_root)
                    del_mb = b_del / (1024 * 1024)
                    del_sz = f"{del_mb/1024:.1f} GB" if del_mb >= 1024 else f"{del_mb:.0f} MB"
                    print(f"\n ✔ Successfully purged {n_del} file(s) ({del_sz}) across {len(analyzer.teslacam_dirs)} drive(s).")
                    print(f" ✔ Updated {n_tombs} tombstone(s) to 'purged' in Tessie/dashcam_tombstones.json.\n")
                    analyzer.index_footage()
                    s_dt = event["start_dt"]
                    e_dt = event.get("end_dt", s_dt + timedelta(seconds=120))
                    event["clips"] = [c for c in analyzer.footage_db if s_dt <= c["dt"] < e_dt]
                else:
                    print(" Purge cancelled.\n")
                continue

            is_flag_del = False
            is_unflag = False
            is_preview = False
            is_list_sei = False
            target_str = choice

            if choice.startswith("l ") or choice.startswith("list "):
                is_list_sei = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("l", "list"):
                is_list_sei = True
                target_str = "all"
            elif choice.startswith("f ") or choice.startswith("flag ") or choice.startswith("d ") or choice.startswith("del ") or choice.startswith("delete "):
                is_flag_del = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("f", "flag", "d", "del", "delete"):
                is_flag_del = True
                target_str = input("Select footage item(s) to flag for deletion [e.g. 1, 1-3, 'all']: ").strip().lower()
            elif choice.startswith("u ") or choice.startswith("unflag "):
                is_unflag = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("u", "unflag"):
                is_unflag = True
                target_str = input("Select footage item(s) to unflag [e.g. 1, 1-3, 'all']: ").strip().lower()
            elif choice.startswith("v ") or choice.startswith("view ") or choice.startswith("play "):
                is_preview = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("v", "view", "play"):
                is_preview = True
                target_str = input("Select footage item to preview [1-N]: ").strip().lower()

            selected_indices = parse_selection_indices(target_str, len(packages))
            if not selected_indices:
                print("Invalid choice. Enter one of:")
                for _opt in ("item numbers (e.g. 1, 6-9, 'all')", "[l <#>] list / inspect", "[v <#>] preview",
                             "[f <#>] flag", "[u <#>] unflag", "[P]urge", "[r]eview", "[o]pen", "[b]ack", "[q]uit"):
                    print(f"  {_opt}")
                print()
                continue

            selected_pkgs = [p for p in packages if p["pkg_idx"] in selected_indices]

            if is_list_sei:
                display_package_sei_breakdown(selected_pkgs, analyzer)
                continue

            if is_preview:
                for idx_val in selected_indices:
                    matching = [p for p in packages if p["pkg_idx"] == idx_val]
                    if matching:
                        open_footage_package(matching[0], analyzer, reveal_clip=True, ask_preference=True)
                continue
            
            if is_flag_del:
                n_flag, b_flag = flag_footage_packages_for_deletion(selected_pkgs, analyzer)
                f_mb = b_flag / (1024 * 1024)
                f_sz = f"{f_mb/1024:.1f} GB" if f_mb >= 1024 else f"{f_mb:.0f} MB"
                num_drives = max(1, len(analyzer.teslacam_dirs))
                mult_mb = (b_flag * num_drives) / (1024 * 1024)
                mult_sz = f"{mult_mb/1024:.1f} GB" if mult_mb >= 1024 else f"{mult_mb:.0f} MB"
                print(f"\n ✔ Flagged {n_flag} footage package(s) for deletion (~{f_sz}/drive ➔ ~{mult_sz} across {num_drives} drive(s)).")
                print("   Files remain on disk until explicitly purged via 'sync_dashcam.sh' or [P]urge.\n")
                continue

            if is_unflag:
                n_unflag = unflag_footage_packages(selected_pkgs, analyzer)
                print(f"\n ✔ Unflagged {n_unflag} footage package(s).\n")
                continue

            all_selected_files = []
            for p in selected_pkgs:
                all_selected_files.extend(p.get("files", []))
            ev_date_str = event["start_dt"].strftime("%Y-%m-%d") if event.get("start_dt") else None
            copy_clips_locally(all_selected_files, analyzer, date_or_str=ev_date_str, ask_preference=True)
            continue

        except (KeyboardInterrupt, EOFError):
            break


def parse_range_selection(raw_str, max_val):
    """Parses range strings like '11', '16-20', '1,3,5', 'all' into sorted list of integers."""
    raw_str = raw_str.strip().lower()
    if raw_str in ("all", "*"):
        return list(range(1, max_val + 1))
    results = set()
    parts = raw_str.replace(" ", ",").split(",")
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if "-" in p:
            sub = p.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                s, e = int(sub[0]), int(sub[1])
                for v in range(min(s, e), max(s, e) + 1):
                    if 1 <= v <= max_val:
                        results.add(v)
        elif p.isdigit():
            v = int(p)
            if 1 <= v <= max_val:
                results.add(v)
    return sorted(list(results))


def batch_flag_events_interactive(events, choice_str, analyzer, is_unflag=False):
    """
    Interactively flags or unflags footage packages belonging to selected events (e.g. '11', '16-20', 'home', 'all').
    Renders an unambiguous preview table of exactly what ranges, folders, and sizes are being staged.
    """
    raw_target = choice_str.strip()
    for pfx in ["unflag", "flag", "delete", "del", "f", "d", "u"]:
        if raw_target.lower().startswith(pfx + " "):
            raw_target = raw_target[len(pfx):].strip()
            break
            
    if not raw_target:
        print("⚠️  Please specify event number(s), range, or 'home' (e.g. 'f 1', 'f 16-20', 'f home', 'f all').\n")
        return

    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    tombstones_data = load_tombstones(master_dir)
    lookup_sets = get_tombstone_lookup_sets(tombstones_data)

    is_home_target = raw_target.lower() in ("home", "h", "a", "auto", "autoflag", "auto-flag")

    if is_home_target:
        packages_to_process = []
        selected_events = []
        for ev in events:
            if ev.get("type") == "parked" and is_home_event(ev, analyzer):
                pkgs = build_timeline_footage_packages(ev)
                for p in pkgs:
                    if p.get("cat") == "Recent":
                        if is_unflag:
                            if is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                                packages_to_process.append(p)
                                if ev not in selected_events:
                                    selected_events.append(ev)
                        else:
                            if not is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                                packages_to_process.append(p)
                                if ev not in selected_events:
                                    selected_events.append(ev)
        if not packages_to_process:
            if is_unflag:
                print("\nℹ️  No flagged Home Recent packages found on this day.\n")
            else:
                print("\nℹ️  No unflagged Home Recent packages found on this day.\n")
            return
        idx_title = f"HOME RECENT (#{','.join(str(e['event_idx']) for e in selected_events)})"
    else:
        selected_indices = parse_range_selection(raw_target, len(events))
        if not selected_indices:
            print(f"⚠️  No valid events matching '{raw_target}'. Enter event numbers 1-{len(events)} (e.g. '16-20', 'home', or 'all').\n")
            return

        target_events = [ev for ev in events if ev["event_idx"] in selected_indices]
        packages_to_process = []
        for ev in target_events:
            packages_to_process.extend(build_timeline_footage_packages(ev))

        if not packages_to_process:
            print(f"\n⚠️  No footage packages found in selected event(s) #{','.join(str(i) for i in selected_indices)}.\n")
            return
        idx_title = f"EVENTS #{min(selected_indices)}–#{max(selected_indices)}" if len(selected_indices) > 1 else f"EVENT #{selected_indices[0]}"

    if is_unflag:
        n_unflag = unflag_footage_packages(packages_to_process, analyzer)
        print(f"\n✔ Unflagged {n_unflag} footage package(s).\n")
        return

 # Unambiguous preview table before flagging
    render_staging_preview_table(packages_to_process, idx_title, analyzer)

    try:
        conf = input(f" Confirm staging these {len(packages_to_process)} package(s) for deletion? [Y/n]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        conf = "n"

    if conf in ("", "y", "yes"):
        n_flag, b_flag = flag_footage_packages_for_deletion(packages_to_process, analyzer)
        print(f"\n ✔ Successfully flagged {n_flag} footage package(s) for deletion.")
        print("   Files remain on disk until explicitly purged via 'sync_dashcam.sh' or [P]urge.\n")
    else:
        print("\n Flagging cancelled.\n")


def display_timeline(target_date, analyzer, compact=False):
    """24-Hour Event Timeline: Vehicle state & footage breakdown across parked/driving events."""
    curr_date = target_date
    w_idx = 5
    w_time = 15
    w_rec = 6
    w_sav = 6
    w_sen = 6

    while True:
        events, day_start, status_msg = analyzer.get_timeline_data(curr_date)
        title = f" 24-Hour Event Timeline: {day_start.strftime('%A, %d %B %Y')}"
        
        if not events:
            total_inner = 70
            msg = status_msg or "No telemetry or footage records found for this date."
            print(f"┌{'─'*total_inner}┐")
            print(f"│{title:<{total_inner}}│")
            print(f"├{'─'*total_inner}┤")
            print(f"│ {msg:<{total_inner-1}}│")
            print(f"└{'─'*total_inner}┘")
            
            if not sys.stdin.isatty():
                break
                
            try:
                print("Navigation:")
                print("  [p]rev day")
                print("  [n]ext day")
                print("  [b]ack")
                print("  [q]uit")
                choice = input("> ").strip().lower()
                if choice == "q":
                    sys.exit(0)
                elif choice in ["b", "back"]:
                    break
                elif choice in ["p", "prev"]:
                    curr_date = curr_date - timedelta(days=1)
                elif choice in ["n", "next"]:
                    curr_date = curr_date + timedelta(days=1)
                else:
                    print("Invalid choice.")
                continue
            except (KeyboardInterrupt, EOFError):
                break

        master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
        tombstones_data = load_tombstones(master_dir)
        lookup_sets = get_tombstone_lookup_sets(tombstones_data)

 # Expand w_act dynamically so that no route or location line gets truncated or broken
        max_act_len = max((display_len(ev["activity"]) + 4 for ev in events), default=50)
        w_act = max(60, max_act_len + 3)
        col_widths = [w_idx, w_time, w_rec, w_sav, w_sen, w_act]
        total_inner = sum(col_widths) + len(col_widths) - 1
        
        total_recent_mins = sum(ev["recent_mins"] for ev in events)
        total_saved_mins = sum(ev["saved_mins"] for ev in events)
        total_sentry_mins = sum(ev["sentry_mins"] for ev in events)
        has_any_footage = (total_recent_mins + total_saved_mins + total_sentry_mins) > 0
        
        h_row = [f" {'#':^3} ", " Time Window", "🔄", "💾", "🔴", " Vehicle State & Route / Location"]

        print(format_box_line("┌", "─", "┐", [total_inner]))
        print(f"│{pad_display(title, total_inner, 'left')}│")
        print(format_box_line("├", "┬", "┤", col_widths))
        print(format_row(h_row, col_widths, aligns=["center", "left", "center", "center", "center", "left"]))
        print(format_box_line("├", "┼", "┤", col_widths))
        
        total_flagged_mins = 0
        home_recent_pkgs = []
        for ev in events:
            r_str = format_duration_short(ev['recent_mins'])
            s_str = format_duration_short(ev['saved_mins'])
            sn_str = format_duration_short(ev['sentry_mins'])
            
            c_idx = f" [{ev['event_idx']:>2}]"
            c_time = " " + ev['time_str']
            c_rec = r_str
            c_sav = s_str
            c_sen = sn_str
            
            ev_pkgs = build_timeline_footage_packages(ev)
            is_ev_flagged = any(is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets) for p in ev_pkgs) if ev_pkgs else False
            if is_ev_flagged:
                total_flagged_mins += (ev['recent_mins'] + ev['saved_mins'] + ev['sentry_mins'])

            if ev.get("type") == "parked" and is_home_event(ev, analyzer):
                for p in ev_pkgs:
                    if p.get("cat") == "Recent" and not is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                        home_recent_pkgs.append(p)

            full_act_text = ev["activity"].strip() + (" 🗑️" if is_ev_flagged else "")
            act_lines = wrap_text_display(full_act_text, w_act - 2)
            c_act = " " + act_lines[0]
            row_vals = [c_idx, c_time, c_rec, c_sav, c_sen, c_act]
            print(format_row(row_vals, col_widths, aligns=["center", "left", "center", "center", "center", "left"]))
            
            for extra in act_lines[1:]:
                extra_vals = ["", "", "", "", "", "   " + extra]
                print(format_row(extra_vals, col_widths, aligns=["left", "left", "left", "left", "left", "left"]))
            
        print(format_box_line("├", "┴", "┤", col_widths))
        if not has_any_footage:
            foot_msg = " No local footage on archive SSD. Run 'sync_dashcam.sh' to backup from car."
            print(f"│{pad_display(foot_msg, total_inner, 'left')}│")
        else:
            rec_tot = format_duration_short(total_recent_mins)
            sav_tot = format_duration_short(total_saved_mins)
            sen_tot = format_duration_short(total_sentry_mins)
            flag_note = f" (🗑️ {format_duration_short(total_flagged_mins)} staged for purge)" if total_flagged_mins > 0 else ""
            foot_msg = f" Footage Totals: 🔄 {rec_tot} | 💾 {sav_tot} | 🔴 {sen_tot}{flag_note}"
            foot_pad = pad_display(foot_msg, total_inner, "left")
            print(f"│{foot_pad}│")
        print(format_box_line("└", "─", "┘", [total_inner]))
            
        if not sys.stdin.isatty():
            break
            
        try:
            n_fl = len(get_flagged_tombstones(master_dir))
            print("\nAction:")
            print(f"  [1-{len(events)}]  Select Event to drill down and inspect footage (or enter HH:MM)")
            print("  [f <#>] Flag event footage for deletion (e.g. 'f 1', 'f 2-5', 'f all')")
            print("  [u <#>] Unflag event footage (e.g. 'u 1', 'u 2-5', 'u all')")
            if home_recent_pkgs:
                print(f"  [a]     Auto-flag Home Recent footage ({len(home_recent_pkgs)} package{'s' if len(home_recent_pkgs) != 1 else ''})")
            if has_any_footage:
                print("  [r]     Review day (step through event footage in Quick Look)")
            if n_fl > 0:
                print(f"  [F]     View flagged deletions menu ({n_fl} staged)")
            print("  [p]     Previous day")
            print("  [n]     Next day")
            print("  [b]     Back to day trips")
            print("  [q]     Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["f", "flagged"]:
                display_flagged_deletions_menu(analyzer)
                continue
            elif choice in ["a", "auto", "home", "autoflag", "auto-flag"]:
                batch_flag_events_interactive(events, "home", analyzer, is_unflag=False)
                continue
            elif any(choice.startswith(pfx + " ") for pfx in ["f", "d", "flag", "del", "delete"]):
                batch_flag_events_interactive(events, choice, analyzer, is_unflag=False)
                continue
            elif any(choice.startswith(pfx + " ") for pfx in ["u", "unflag"]):
                batch_flag_events_interactive(events, choice, analyzer, is_unflag=True)
                continue
            elif choice in ["r", "review", "review day"]:
                all_day_pkgs = []
                for ev in events:
                    all_day_pkgs.extend(build_timeline_footage_packages(ev))
                if not all_day_pkgs:
                    print("\n⚠️  No footage packages recorded on this day.\n")
                else:
                    run_interactive_footage_review(all_day_pkgs, analyzer, context_label=day_start.strftime('%A, %d %B %Y'))
                continue
            elif choice in ["p", "prev"]:
                curr_date = curr_date - timedelta(days=1)
            elif choice in ["n", "next"]:
                curr_date = curr_date + timedelta(days=1)
            else:
                matched_ev = None
                if choice.isdigit():
                    val = int(choice)
                    for ev in events:
                        if ev["event_idx"] == val:
                            matched_ev = ev
                            break
                elif ":" in choice:
                    time_part = choice.replace(".", ":")
                    for ev in events:
                        if ev["start_dt"].strftime("%H:%M") == time_part or time_part in ev["time_str"]:
                            matched_ev = ev
                            break
                if matched_ev:
                    display_timeline_event_details(matched_ev, analyzer)
                else:
                    print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break


def display_days_menu(days_dict, title, analyzer, can_go_back=False):
    """Level 1: Display days summary table and allow drilling down to any day."""
    sorted_days = sorted(days_dict.keys(), reverse=True)
    
    while True:
        total_trips = sum(len(v) for v in days_dict.values())
        
 # Precompute rows to dynamically measure max column widths
        rows_data = []
        for idx, d_str in enumerate(sorted_days):
            day_trips = days_dict[d_str]
            dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
            total_km = sum(t["dist_km"] for t in day_trips)
            total_mins = sum(t["dur_min"] for t in day_trips)
            hours, mins = divmod(total_mins, 60)
            time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
            
            day_cats = set()
            for t in day_trips:
                _, cats = analyzer.get_trip_footage_summary(t)
                day_cats.update(cats)
                
            notable = extract_notable_destinations(day_trips, max_places=3)
            
            rows_data.append({
                "idx": f" [{idx+1}]",
                "date": f" {dt_obj.strftime('%a %d %b %Y')}",
                "trips": f" {len(day_trips)}",
                "time": f" {time_str}",
                "dist": f" {total_km:.1f} km",
                "notable": f" {notable}",
                "sav": "📹" if "Saved" in day_cats else "·",
                "sen": "🛡️" if "Sentry" in day_cats else "·",
                "rec": "🕒" if "Recent" in day_cats else "·"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_date = max(len(" Date "), max((display_len(r["date"]) + 1 for r in rows_data), default=18))
        w_trips = max(len(" Trips "), max((display_len(r["trips"]) + 1 for r in rows_data), default=8))
        w_time = max(len(" Time "), max((display_len(r["time"]) + 1 for r in rows_data), default=11))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=13))
        w_notable = max(len(" Notable Destinations "), max((display_len(r["notable"]) + 1 for r in rows_data), default=25))

        w_sav = 7
        w_sen = 8
        w_rec = 8
        w_foot_total = w_sav + w_sen + w_rec + 2  # 25

        left_widths = [w_idx, w_date, w_trips, w_time, w_dist, w_notable]
        left_headers = [" #", " Date", " Trips", " Time", " Distance", " Notable Destinations"]

        total_inner = sum(left_widths) + len(left_widths) + w_foot_total
        title_str = f" 📍 {title} ({total_trips} Trips Across {len(sorted_days)} Days)"
        t_len = display_len(title_str)
        if t_len + 2 > total_inner:
            w_notable += (t_len + 2 - total_inner)
            left_widths[5] = w_notable
            total_inner = sum(left_widths) + len(left_widths) + w_foot_total

        border_top = "┌" + "─" * total_inner + "┐"
        border_top_header = "├" + "┬".join("─"*w for w in left_widths) + "┬" + "─"*w_foot_total + "┤"
        border_sub = "│" + "│".join(" "*w for w in left_widths) + "├" + "─"*w_sav + "┬" + "─"*w_sen + "┬" + "─"*w_rec + "┤"
        border_data = "├" + "┼".join("─"*w for w in left_widths) + "┼" + "─"*w_sav + "┼" + "─"*w_sen + "┼" + "─"*w_rec + "┤"
        border_bot = "└" + "┴".join("─"*w for w in left_widths) + "┴" + "─"*w_sav + "┴" + "─"*w_sen + "┴" + "─"*w_rec + "┘"
        
        print(f"\n{border_top}")
        print(format_title_line(title_str, total_inner, "left"))
        print(border_top_header)
        
        row1_cols = [pad_display(h, w) for h, w in zip(bold_headers(left_headers), left_widths)] + [pad_display(f"\033[1mFootage\033[0m", w_foot_total, "center")]  # TODO-015: bolded to match analyse_charging.py header convention
        print("│" + "│".join(row1_cols) + "│")
        print(border_sub)
        
        row2_cols = [" "*w for w in left_widths] + [
            pad_display("Saved", w_sav, "center"),
            pad_display("Sentry", w_sen, "center"),
            pad_display("Recent", w_rec, "center")
        ]
        print("│" + "│".join(row2_cols) + "│")
        print(border_data)
        
        all_widths = left_widths + [w_sav, w_sen, w_rec]
 # idx/trips/time/dist are numbers (with units, or plain for idx/trips) -> right-aligned;
 # date and notable-destinations are free text -> left-aligned; footage icons stay centered.
        all_aligns = ["right", "left", "right", "right", "right", "left", "center", "center", "center"]
        for r in rows_data:
            row_cells = [
                r["idx"] + " ", r["date"], r["trips"] + " ", r["time"] + " ", r["dist"] + " ", r["notable"],
                r["sav"], r["sen"], r["rec"]
            ]
            print(format_row(row_cells, all_widths, all_aligns))

        print(border_bot)
        try:
            print("\nAction:")
            print(f"  [1-{len(sorted_days)}] Select Day to drill down into drive list")
            print("  [a]    View all drives across all days")
            if can_go_back:
                print("  [b]    Back to months overview")
            print("  [q]    Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"] and can_go_back:
                break
            elif choice in ["a", "all"]:
                drill_down_all_days(sorted_days, days_dict, analyzer, title)
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_days):
                selected_day = sorted_days[int(choice)-1]
                drill_down_day(selected_day, days_dict[selected_day], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break


def display_months_menu(days_dict, analyzer):
    """Level 0: Display months overview table and allow picking a month to inspect."""
    months_dict = defaultdict(dict)
    for d_str, trips in days_dict.items():
        m_key = d_str[:7]
        months_dict[m_key][d_str] = trips
    
    sorted_months = sorted(months_dict.keys(), reverse=True)
    if len(sorted_months) == 1:
        m_key = sorted_months[0]
        dt_m = datetime.strptime(m_key + "-01", "%Y-%m-%d")
        display_days_menu(months_dict[m_key], dt_m.strftime("%B %Y"), analyzer, can_go_back=False)
        return
        
    while True:
 # Precompute rows to dynamically measure max column widths
        rows_data = []
        for idx, m_key in enumerate(sorted_months):
            m_days = months_dict[m_key]
            m_trips = [t for day_list in m_days.values() for t in day_list]
            dt_m = datetime.strptime(m_key + "-01", "%Y-%m-%d")
            total_km = sum(t["dist_km"] for t in m_trips)
            total_mins = sum(t["dur_min"] for t in m_trips)
            hours, mins = divmod(total_mins, 60)
            time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
            notable = extract_notable_destinations(m_trips, max_places=4)
            
            rows_data.append({
                "idx": f" [{idx+1}]",
                "month": f" {dt_m.strftime('%B %Y')}",
                "days": f" {len(m_days)}",
                "trips": f" {len(m_trips)}",
                "time": f" {time_str}",
                "dist": f" {total_km:,.1f} km",
                "notable": f" {notable}"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_month = max(len(" Month "), max((display_len(r["month"]) + 1 for r in rows_data), default=18))
        w_days = max(len(" Days "), max((display_len(r["days"]) + 1 for r in rows_data), default=8))
        w_trips = max(len(" Trips "), max((display_len(r["trips"]) + 1 for r in rows_data), default=8))
        w_time = max(len(" Time "), max((display_len(r["time"]) + 1 for r in rows_data), default=11))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=13))
        w_notable = max(len(" Notable Destinations "), max((display_len(r["notable"]) + 1 for r in rows_data), default=25))
        
        total_inner = w_idx + w_month + w_days + w_trips + w_time + w_dist + w_notable + 6
        total_all_trips = sum(len(trips) for trips in days_dict.values())
        title = f" 🗓️ TESSIE DRIVES BY MONTH ({total_all_trips:,} Trips Across {len(days_dict)} Days)"
        t_len = display_len(title)
        if t_len + 2 > total_inner:
            w_notable += (t_len + 2 - total_inner)
            total_inner = w_idx + w_month + w_days + w_trips + w_time + w_dist + w_notable + 6

        border_top = format_box_line("┌", "─", "┐", [total_inner])
        border_top_header = format_box_line("├", "┬", "┤", [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable])
        border_data_divider = format_box_line("├", "┼", "┤", [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable])
        border_bot = format_box_line("└", "┴", "┘", [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable])
        
        print(f"\n{border_top}")
        print(format_title_line(title, total_inner, "left"))
        print(border_top_header)
        
        m_widths = [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable]
        m_headers = [" #", " Month", " Days", " Trips", " Time", " Distance", " Notable Destinations"]
 # idx/days/trips/time/dist are numbers (with units, or plain for idx/days/trips) -> right-aligned;
 # month and notable-destinations are free text -> left-aligned.
        m_aligns = ["right", "left", "right", "right", "right", "right", "left"]
        print(format_row(bold_headers(m_headers), m_widths))  # TODO-015: bolded to match analyse_charging.py header convention
        print(border_data_divider)
        
        for r in rows_data:
            m_cells = [r["idx"] + " ", r["month"], r["days"] + " ", r["trips"] + " ", r["time"] + " ", r["dist"] + " ", r["notable"]]
            print(format_row(m_cells, m_widths, m_aligns))
            
        print(border_bot)
        try:
            print("\nAction:")
            print(f"  [1-{len(sorted_months)}] Select Month to inspect days & drives")
            print("  [a]    View all months combined")
            print("  [t]    Jump to Today's drives")
            print("  [y]    Jump to Yesterday's drives")
            print("  [q]    Quit")
            choice = input("\nChoice: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["a", "all"]:
                display_days_menu(days_dict, "All Drive History", analyzer, can_go_back=True)
            elif choice in ["t", "today"]:
                today_str = datetime.now().strftime("%Y-%m-%d")
                if today_str in days_dict:
                    drill_down_day(today_str, days_dict[today_str], analyzer)
                else:
                    print("No drives recorded for today.")
            elif choice in ["y", "yesterday"]:
                y_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                if y_str in days_dict:
                    drill_down_day(y_str, days_dict[y_str], analyzer)
                else:
                    print("No drives recorded for yesterday.")
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_months):
                selected_m = sorted_months[int(choice)-1]
                dt_m = datetime.strptime(selected_m + "-01", "%Y-%m-%d")
                display_days_menu(months_dict[selected_m], dt_m.strftime("%B %Y"), analyzer, can_go_back=True)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break

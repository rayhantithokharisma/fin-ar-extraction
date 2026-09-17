#!/usr/bin/env python3
"""
Organize IDX Financial Statements and Annual Reports.

This script scans a directory containing downloaded IDX PDF files, groups them by
ticker, filters out cover letters (<15 pages for AR), deduplicates downloads (e.g. '(1)' copies),
selects the primary Annual Report when multiple attachments exist (att1, att2, etc.),
and organizes them into clean ticker subfolders.

When symmetry enforcement is enabled (default), each ticker folder only accepts years that
contain BOTH an Annual Report and a Financial Statement (e.g. 2022-2025 all 2 files,
or 2024-2025 all 2 files). Any unpaired orphan files or tickers with no paired years are deleted.
"""

import argparse
import logging
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: PyMuPDF (fitz) is required. Install it using 'pip install pymupdf'.", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("organize_ticker")

PATTERN_FS = re.compile(
    r"financialstatement-(\d{4})-tahunan-([a-zA-Z0-9]+)(?:\s*\(\d+\))?\.pdf",
    re.IGNORECASE
)
PATTERN_AR = re.compile(
    r"annualreport(\d{4})-([a-zA-Z0-9]+)(?:-att(\d+))?(?:\s*\(\d+\))?\.pdf",
    re.IGNORECASE
)

STANDARDIZED_AR = re.compile(r"annualreport(\d{4})\.pdf", re.IGNORECASE)
STANDARDIZED_FS = re.compile(r"financialstatement(\d{4})\.pdf", re.IGNORECASE)


class FileRecord:
    def __init__(self, filename: str, full_path: Path):
        self.filename = filename
        self.path = full_path
        self.size = full_path.stat().st_size
        self.doc_type: Optional[str] = None  # 'AR' or 'FS'
        self.year: Optional[int] = None
        self.ticker: Optional[str] = None
        self.att: int = 0
        self.has_paren: bool = bool(re.search(r"\(\d+\)", filename))
        self.pages: int = 0
        self._parsed = self._parse_name()

    def _parse_name(self) -> bool:
        m_fs = PATTERN_FS.search(self.filename)
        if m_fs:
            year_str, ticker_str = m_fs.groups()
            self.doc_type = "FS"
            self.year = int(year_str)
            self.ticker = ticker_str.lower()
            self.att = 0
            return True

        m_ar = PATTERN_AR.search(self.filename)
        if m_ar:
            year_str, ticker_str, att_str = m_ar.groups()
            self.doc_type = "AR"
            self.year = int(year_str)
            self.ticker = ticker_str.lower()
            self.att = int(att_str) if att_str else 0
            return True

        return False

    def read_page_count(self) -> int:
        if self.pages > 0:
            return self.pages
        try:
            doc = fitz.open(str(self.path))
            self.pages = doc.page_count
            doc.close()
        except Exception as e:
            logger.warning(f"Failed reading pages for {self.filename}: {e}")
            self.pages = 0
        return self.pages

    def read_snippet(self, max_pages: int = 4) -> str:
        snippet = ""
        try:
            doc = fitz.open(str(self.path))
            for p in range(min(max_pages, doc.page_count)):
                snippet += doc[p].get_text("text").lower() + " "
            doc.close()
        except Exception:
            pass
        return snippet


def select_best_document(
    candidates: List[FileRecord],
    doc_type: str,
    ar_min_pages: int = 15,
    fs_min_pages: int = 10
) -> Tuple[Optional[FileRecord], List[Tuple[FileRecord, str]]]:
    """
    Selects the single best document from a list of candidates for a (ticker, doc_type, year) group.
    Returns (winner, [(discarded_record, reason), ...])
    """
    discarded = []

    # Ensure all candidates have page counts read
    for c in candidates:
        c.read_page_count()

    if doc_type == "FS":
        valid = [c for c in candidates if c.pages >= fs_min_pages]
        invalid = [c for c in candidates if c.pages < fs_min_pages]
        for inv in invalid:
            discarded.append((inv, f"FS below threshold ({inv.pages} pages < {fs_min_pages})"))

        if not valid:
            return None, discarded

        valid.sort(key=lambda x: (not x.has_paren, x.pages, x.size), reverse=True)
        winner = valid[0]
        for other in valid[1:]:
            discarded.append((other, f"Duplicate FS of {winner.filename}"))
        return winner, discarded

    else:  # AR (Annual Report)
        valid = [c for c in candidates if c.pages >= ar_min_pages]
        invalid = [c for c in candidates if c.pages < ar_min_pages]
        for inv in invalid:
            discarded.append((inv, f"AR cover letter / below threshold ({inv.pages} pages < {ar_min_pages})"))

        if not valid:
            return None, discarded

        if len(valid) == 1:
            winner = valid[0]
            return winner, discarded

        scored = []
        for c in valid:
            txt = c.read_snippet(max_pages=4)
            has_ar_kw = ("annual report" in txt or "laporan tahunan" in txt)
            is_pure_sr = ("sustainability" in txt or "keberlanjutan" in txt) and not has_ar_kw

            score = 0
            if has_ar_kw:
                score += 10
            if is_pure_sr:
                score -= 10

            scored.append((score, c.pages, c.size, -c.att, c))

        scored.sort(reverse=True)
        winner = scored[0][4]

        for _, _, _, _, other in scored[1:]:
            discarded.append((other, f"Secondary AR attachment/SR (winner: {winner.filename})"))

        return winner, discarded


def prune_asymmetric_tickers(
    target_dir: Path,
    dry_run: bool = False,
    verbose: bool = False
) -> None:
    """
    Prunes an already organized target directory to keep only folders where AR and FS are symmetrical.
    For each ticker:
      - Deletes any orphan files where only AR or only FS exists for that year.
      - If a ticker has no paired years, the entire ticker folder is deleted.
    """
    logger.info(f"Checking existing ticker subdirectories for symmetry in: {target_dir}")
    subdirs = [d for d in target_dir.iterdir() if d.is_dir()]
    logger.info(f"Found {len(subdirs)} ticker subdirectories.")

    deleted_folders_count = 0
    deleted_orphans_count = 0
    kept_tickers: Dict[str, List[int]] = {}

    for d in sorted(subdirs):
        ar_years: Dict[int, Path] = {}
        fs_years: Dict[int, Path] = {}
        other_files: List[Path] = []

        for f in d.iterdir():
            if not f.is_file():
                continue
            m_ar = STANDARDIZED_AR.match(f.name)
            if m_ar:
                ar_years[int(m_ar.group(1))] = f
                continue
            m_fs = STANDARDIZED_FS.match(f.name)
            if m_fs:
                fs_years[int(m_fs.group(1))] = f
                continue
            other_files.append(f)

        common_years = sorted(set(ar_years.keys()) & set(fs_years.keys()))

        if not common_years:
            # No paired years: delete the entire folder
            if verbose:
                logger.info(f"Delete asymmetric folder (0 pairs): {d.name}")
            if not dry_run:
                shutil.rmtree(d)
            deleted_folders_count += 1
        else:
            # Delete orphan files in this folder
            for y, f in ar_years.items():
                if y not in common_years:
                    if verbose:
                        logger.info(f"Delete orphan file: {d.name}/{f.name} (no FS for {y})")
                    if not dry_run:
                        f.unlink()
                    deleted_orphans_count += 1

            for y, f in fs_years.items():
                if y not in common_years:
                    if verbose:
                        logger.info(f"Delete orphan file: {d.name}/{f.name} (no AR for {y})")
                    if not dry_run:
                        f.unlink()
                    deleted_orphans_count += 1

            for f in other_files:
                if verbose:
                    logger.info(f"Delete extraneous file: {d.name}/{f.name}")
                if not dry_run:
                    f.unlink()
                deleted_orphans_count += 1

            kept_tickers[d.name] = common_years

    year_distribution = Counter(len(years) for years in kept_tickers.values())

    logger.info("=" * 60)
    logger.info("SYMMETRY PRUNING COMPLETE")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'APPLIED'}")
    logger.info(f"Total ticker folders kept: {len(kept_tickers)}")
    logger.info(f"Total asymmetric ticker folders deleted: {deleted_folders_count}")
    logger.info(f"Total orphan files deleted: {deleted_orphans_count}")
    logger.info("Distribution of kept tickers by symmetrical years:")
    for num_years in sorted(year_distribution.keys(), reverse=True):
        count = year_distribution[num_years]
        logger.info(f"  {num_years} years ({num_years * 2} files): {count} tickers")
    logger.info("=" * 60)


def organize_directory(
    target_dir: Path,
    ar_min_pages: int = 15,
    fs_min_pages: int = 10,
    symmetric_only: bool = True,
    dry_run: bool = False,
    verbose: bool = False
) -> None:
    target_dir = target_dir.resolve()
    if not target_dir.is_dir():
        logger.error(f"Target directory does not exist: {target_dir}")
        sys.exit(1)

    logger.info(f"Scanning target directory: {target_dir}")
    if dry_run:
        logger.info(">>> DRY RUN MODE: No files will be moved or deleted <<<")

    # Scan all files directly inside target_dir (skip subdirectories)
    files = [f for f in target_dir.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"]

    if not files:
        logger.info("No loose PDF files found in root directory.")
        if symmetric_only:
            prune_asymmetric_tickers(target_dir, dry_run=dry_run, verbose=verbose)
        return

    logger.info(f"Found {len(files)} loose PDF files in root directory.")

    # Parse records
    parsed_records: List[FileRecord] = []
    unmatched_files: List[Path] = []

    for f in files:
        record = FileRecord(f.name, f)
        if record._parsed:
            parsed_records.append(record)
        else:
            unmatched_files.append(f)

    logger.info(f"Parsed: {len(parsed_records)} files. Unmatched: {len(unmatched_files)}")
    if unmatched_files:
        logger.warning(f"Unmatched files in directory: {[f.name for f in unmatched_files[:5]]}")

    # Group by (ticker, doc_type, year)
    grouped: Dict[Tuple[str, str, int], List[FileRecord]] = defaultdict(list)
    for r in parsed_records:
        grouped[(r.ticker, r.doc_type, r.year)].append(r)

    logger.info(f"Total distinct (ticker, doc_type, year) groups: {len(grouped)}")

    # Selection process
    selected_map: Dict[Tuple[str, str, int], FileRecord] = {}
    all_discarded: List[Tuple[FileRecord, str]] = []

    for group_key, candidates in grouped.items():
        ticker, doc_type, year = group_key
        winner, discarded = select_best_document(
            candidates, doc_type, ar_min_pages=ar_min_pages, fs_min_pages=fs_min_pages
        )
        if winner:
            selected_map[group_key] = winner
        all_discarded.extend(discarded)

    # Symmetry Filter: Only keep years where BOTH AR and FS exist
    if symmetric_only:
        ticker_ar_years: Dict[str, Set[int]] = defaultdict(set)
        ticker_fs_years: Dict[str, Set[int]] = defaultdict(set)
        for (ticker, doc_type, year) in selected_map.keys():
            if doc_type == "AR":
                ticker_ar_years[ticker].add(year)
            elif doc_type == "FS":
                ticker_fs_years[ticker].add(year)

        filtered_selected_map: Dict[Tuple[str, str, int], FileRecord] = {}
        for (ticker, doc_type, year), record in selected_map.items():
            common_years = ticker_ar_years[ticker] & ticker_fs_years[ticker]
            if year in common_years:
                filtered_selected_map[(ticker, doc_type, year)] = record
            else:
                all_discarded.append(
                    (record, f"Unpaired {doc_type} for year {year} (symmetry enforced)")
                )
        selected_map = filtered_selected_map

    logger.info(f"Selected files to keep: {len(selected_map)}")
    logger.info(f"Discarded files (duplicates/cover letters/unpaired): {len(all_discarded)}")

    # Ticker organization mapping
    ticker_files: Dict[str, List[Tuple[FileRecord, str]]] = defaultdict(list)
    for (ticker, doc_type, year), record in selected_map.items():
        dest_filename = f"AnnualReport{year}.pdf" if doc_type == "AR" else f"FinancialStatement{year}.pdf"
        ticker_files[ticker].append((record, dest_filename))

    logger.info(f"Total tickers to organize: {len(ticker_files)}")

    # Count distribution of files per ticker
    distribution = Counter(len(flist) for flist in ticker_files.values())
    logger.info("Files per ticker distribution:")
    for count in sorted(distribution.keys()):
        logger.info(f"  {count} files: {distribution[count]} tickers")

    # Execution Phase:
    moved_count = 0
    deleted_count = 0

    # Execute moves
    for ticker, items in ticker_files.items():
        ticker_dir = target_dir / ticker
        if not dry_run:
            ticker_dir.mkdir(parents=True, exist_ok=True)

        for record, dest_name in items:
            dest_path = ticker_dir / dest_name
            if verbose:
                logger.info(f"Move: {record.filename} -> {ticker}/{dest_name}")
            if not dry_run:
                if dest_path.exists() and dest_path.resolve() != record.path.resolve():
                    dest_path.unlink()
                shutil.move(str(record.path), str(dest_path))
            moved_count += 1

    # Execute deletions of discarded files
    for record, reason in all_discarded:
        if verbose:
            logger.info(f"Delete ({reason}): {record.filename}")
        if not dry_run:
            if record.path.exists():
                record.path.unlink()
        deleted_count += 1

    # Final Summary Report
    logger.info("=" * 60)
    logger.info("ORGANIZATION COMPLETE")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'APPLIED'}")
    logger.info(f"Total tickers processed: {len(ticker_files)}")
    logger.info(f"Total target files moved/organized: {moved_count}")
    logger.info(f"Total duplicate/discarded files deleted: {deleted_count}")
    logger.info(f"Tickers with all 8 files: {distribution.get(8, 0)}")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Organize and prune complete-ticker PDF files into symmetrical ticker subfolders."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path("/Users/Rayhan.Kharisma/Documents/prsnl-prjct/statements-extraction/complete-ticker"),
        help="Path to complete-ticker directory"
    )
    parser.add_argument(
        "--ar-min-pages",
        type=int,
        default=15,
        help="Minimum page count for Annual Reports (default: 15)"
    )
    parser.add_argument(
        "--fs-min-pages",
        type=int,
        default=10,
        help="Minimum page count for Financial Statements (default: 10)"
    )
    parser.add_argument(
        "--no-symmetry",
        action="store_false",
        dest="symmetric_only",
        help="Disable symmetry enforcement (keep unpaired years)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate actions without moving or deleting files"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose move/delete logs for every file"
    )

    args = parser.parse_args()
    organize_directory(
        target_dir=args.dir,
        ar_min_pages=args.ar_min_pages,
        fs_min_pages=args.fs_min_pages,
        symmetric_only=args.symmetric_only,
        dry_run=args.dry_run,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()

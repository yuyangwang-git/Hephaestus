import argparse
import logging
import signal
import sys
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import pyvisa as visa
import csv


class NetworkAnalyzerReader:
    """
    定期从网络分析仪读取数据，并将原始数据和汇总日志保存到本地。
    """

    def __init__(
        self,
        visa_address: str,
        data_dir: Path,
        interval_s: int = 30,
    ) -> None:
        self.visa_address = visa_address
        self.interval_s = interval_s
        self.data_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()

        # VISA 会话管理
        self.rm = visa.ResourceManager()
        self.session = self.rm.open_resource(self.visa_address)

        # 数据目录及汇总文件
        self.data_dir: Path = data_dir
        self.summary_path: Path = self.data_dir / "summary.csv"
        self._setup_data_dir_and_summary()

        # 基线信息
        self.read_count = 0
        self.baseline_time: datetime | None = None
        self.baseline_freq: float | None = None

    def _setup_data_dir_and_summary(self) -> None:
        """确保数据目录存在并初始化汇总文件头。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.summary_path.exists():
            with self.summary_path.open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Timestamp", "ReadCount", "Min_dB",
                    "Frequency_Hz", "TimeDelta_s", "FrequencyShift_Hz"
                ])

    def perform_single_sweep(self) -> None:
        """触发单次扫描并等待完成。"""
        self.session.write("SENS1:SWE:MODE SING")
        self.session.query("*OPC?")

    def _generate_raw_path(self, timestamp: datetime, freq_hz: float) -> Path:
        """生成原始 CSV 文件路径。"""
        timestr = timestamp.strftime("%Y%m%d_%H%M%S")
        fname = f"{timestr}_{int(freq_hz)}Hz.csv"
        return self.data_dir / fname

    def _write_raw_csv(self, path: Path, x_values: list[float], results: list[float]) -> None:
        """将单次扫描数据写入 CSV。"""
        with path.open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Frequency (Hz)", "Magnitude (dB)"])
            for x, y in zip(x_values, results):
                writer.writerow([x, y])

    def _append_summary(self, timestamp: datetime, freq_hz: float, mag_db: float) -> None:
        """追加一行汇总日志，并更新基线数据。"""
        self.read_count += 1
        if self.read_count == 1:
            self.baseline_time = timestamp
            self.baseline_freq = freq_hz
            time_delta = 0.0
            freq_shift = 0.0
        else:
            time_delta = (timestamp - self.baseline_time).total_seconds()  # type: ignore
            freq_shift = freq_hz - (self.baseline_freq or freq_hz)

        with self.summary_path.open('a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                self.read_count,
                f"{mag_db:.2f}",
                f"{freq_hz:.2f}",
                f"{time_delta:.2f}",
                f"{freq_shift:.2f}"
            ])

    def _format_summary(self, timestamp: datetime, freq_hz: float, mag_db: float) -> str:
        return (
            f"[{timestamp:%Y-%m-%d %H:%M:%S}] 第{self.read_count}次读取："
            f"最小值{mag_db:.2f}dB 出现在{freq_hz:.2f}Hz。"
        )

    def read_loop(self) -> None:
        """读取线程：定期触发扫描并推送数据到队列。"""
        while not self.stop_event.is_set():
            try:
                self.perform_single_sweep()
                results = self.session.query_ascii_values("CALC1:MEAS1:DATA:FDATA?")
                x_values = self.session.query_ascii_values("CALC1:MEAS1:X:VAL?")
                timestamp = datetime.now()
                self.data_queue.put((timestamp, x_values, results))
            except Exception:
                logging.exception("读取过程中发生错误，重试中…")
            if self.stop_event.wait(self.interval_s):
                break

    def process_loop(self) -> None:
        """处理线程：消费队列数据，保存文件并写汇总。"""
        while not (self.stop_event.is_set() and self.data_queue.empty()):
            try:
                timestamp, x_values, results = self.data_queue.get(timeout=1)
            except queue.Empty:
                continue

            min_val = min(results)
            idx = results.index(min_val)
            freq_at_min = x_values[idx]

            raw_path = self._generate_raw_path(timestamp, freq_at_min)
            self._write_raw_csv(raw_path, x_values, results)
            self._append_summary(timestamp, freq_at_min, min_val)

            logging.info(
                f"{self._format_summary(timestamp, freq_at_min, min_val)} 文件已保存: {raw_path.name}"
            )
            self.data_queue.task_done()

    def run(self) -> None:
        """启动读取和处理线程，并等待 Ctrl-C 来中断退出。"""
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())

        reader_thread = threading.Thread(target=self.read_loop, daemon=True, name="ReaderThread")
        processor_thread = threading.Thread(target=self.process_loop, daemon=True, name="ProcessorThread")
        reader_thread.start()
        processor_thread.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            logging.info("收到 Ctrl-C，正在停止所有线程…")
            self.stop_event.set()
        finally:
            self.data_queue.join()
            self.session.close()
            self.rm.close()
            logging.info("所有线程结束，退出程序。")


def main() -> None:
    default_visa = 'TCPIP0::Vega::hislip_PXI0_CHASSIS1_SLOT1_INDEX0::INSTR'
    default_dir = Path("data")
    default_interval = 1

    parser = argparse.ArgumentParser(description="网络分析仪数据采集工具")
    parser.add_argument(
        "--visa", default=default_visa,
        help=f"VISA 地址 (默认: {default_visa})"
    )
    parser.add_argument(
        "--dir", type=Path, default=default_dir,
        help=f"数据保存目录 (默认: {default_dir})"
    )
    parser.add_argument(
        "--interval", type=int, default=default_interval,
        help=f"读取间隔（秒） (默认: {default_interval})"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    reader = NetworkAnalyzerReader(
        visa_address=args.visa,
        data_dir=args.dir,
        interval_s=args.interval,
    )
    try:
        reader.run()
    except KeyboardInterrupt:
        logging.info("主程序收到 Ctrl-C，退出。")
        sys.exit(0)


if __name__ == "__main__":
    main()

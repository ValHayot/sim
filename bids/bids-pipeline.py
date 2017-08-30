#!/usr/bin/env python

from pyspark import SparkContext, SparkConf
from bids.grabbids import BIDSLayout
import argparse
import os

def create_RDD(bids_dataset_root,sc):
    layout = BIDSLayout(bids_dataset_root)
    return sc.parallelize(layout.get_subjects())

def list_files_by_participant(bids_dataset, participant_name):
    array = []
    os.chdir(bids_dataset)
    for root, dirs, files in os.walk(bids_dataset):
       for file in files:
          if file.startswith("sub-{0}".format(participant_name)):
             array.append(file)
    return array

def main():
    # Spark initialization
    conf = SparkConf().setAppName("log_analyzer").setMaster("local")
    sc = SparkContext(conf=conf)
    
    parser=argparse.ArgumentParser()
    parser.add_argument("bids_dataset", help="BIDS dataset to be processed")
    args=parser.parse_args()
    bids_dataset = args.bids_dataset
    
    rdd = create_RDD(bids_dataset,sc)
    rdd = rdd.map(lambda x: list_files_by_participant(bids_dataset,x))
    
    print(rdd.collect())
    
# Execute program
if  __name__ == "__main__":
    main()

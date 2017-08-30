#!/usr/bin/env python

from pyspark import SparkContext, SparkConf
from bids.grabbids import BIDSLayout
import argparse, json, os, errno, subprocess, time, tarfile, shutil

def supports_group_analysis(boutiques_descriptor):
    desc = json.load(open(boutiques_descriptor))
    analysis_level_input = None
    for input in desc["inputs"]:
        if input["id"] == "analysis_level":
            analysis_level_input = input
            break
    assert(analysis_level_input),"BIDS app descriptor has no input with id 'analysis_level'"
    assert(analysis_level_input.get("value-choices")),"Input 'analysis_level' of BIDS app descriptor has no 'value-choices' property"   
    return "group" in analysis_level_input["value-choices"]

def create_RDD(bids_dataset_root,sc):
    layout = BIDSLayout(bids_dataset_root)
    return sc.parallelize(layout.get_subjects())

def create_subject_RDD(bids_dataset_root, sc, sub_dir='tar_subjects'):
    layout = BIDSLayout(bids_dataset_root)

    try:
        os.makedirs(sub_dir)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    
    for sub in layout.get_subjects():
        files = layout.get(subject=sub)

        with tarfile.open("tar_subjects/sub-{0}.tar".format(sub), "w") as tar:
            for f in files:
                tar.add(f.filename)
    
    return sc.binaryFiles("tar_subjects")


def pretty_print(result):
    label, log, returncode = result
    if returncode == 0:
        print(" [ SUCCESS ] {0}".format(label))
    else:
        timestamp = str(int(time.time() * 1000))
        filename = "{0}.{1}.err".format(timestamp, label)
        with open(filename,"w") as f:
            f.write(log)
        f.close()
        print(" [ ERROR ({0}) ] {1} - {2}".format(returncode, label, filename))

def write_invocation_file(bids_dataset, output_dir, analysis_level, subject_label, invocation_file):

    # Note: the invocation file format will change soon
    
    # Creates invocation object
    invocation = {}
    invocation["inputs"] = [ ]
    invocation["inputs"].append({"bids_dir": bids_dataset})
    invocation["inputs"].append({"output_dir_name": output_dir})
    if analysis_level == "participant":
        invocation["inputs"].append({"analysis_level": "participant"}) 
        invocation["inputs"].append({"participant_label": subject_label})
    elif analysis_level == "group":
        invocation["inputs"].append({"analysis_level": "group"})
        
    json_invocation = json.dumps(invocation)

    # Writes invocation
    with open(invocation_file,"w") as f:
        f.write(json_invocation)

def get_bids_dataset(data, subject_label):

    filename = 'sub-{0}.tar'.format(subject_label)    
    foldername = 'sub-{0}'.format(subject_label)

    # Save participant byte stream to disk
    with open(filename, 'w') as f:
        f.write(data)

    # Now extract data from tar
    tar = tarfile.open(filename)
    tar.extractall(path=foldername)
    tar.close()

    os.remove(filename)

    return foldername
    

def run_subject_analysis(boutiques_descriptor, data, subject_label, output_dir):

    bids_dataset = get_bids_dataset(data, subject_label)
    invocation_file = "./invocation-{0}.json".format(subject_label)
    write_invocation_file(bids_dataset, output_dir, "participant", subject_label, invocation_file)
    return bosh_exec(boutiques_descriptor, invocation_file, "sub-{0}".format(subject_label))

def run_group_analysis(boutiques_descriptor, bids_dataset, output_dir):
    invocation_file = "./invocation-group.json"
    write_invocation_file(bids_dataset, output_dir, "group", None, invocation_file)
    return bosh_exec(boutiques_descriptor, invocation_file, "group")

def bosh_exec(boutiques_descriptor, invocation_file, label):
    run_command = "localExec.py {0} -i {1} -e -d".format(boutiques_descriptor, invocation_file)
    result = None
    try:
        log = subprocess.check_output(run_command, shell=True, stderr=subprocess.STDOUT)
        result = (label, log, 0)
    except subprocess.CalledProcessError as e:
        result = (label, e.output, e.returncode)
    os.remove(invocation_file)

    try:
        shutil.rmtree(label)
    except:
        pass

    return result

def is_valid_file(parser, arg):
    if not os.path.exists(arg):
        parser.error("The file %s does not exist!" % arg)
    else:
        return open(arg, 'r')

def get_subject_from_fn(filename):
    return filename.split('-')[-1][:-4]

def main():

    # Arguments parsing
    parser=argparse.ArgumentParser()
    parser.add_argument("bids_app_boutiques_descriptor", help="Boutiques descriptor of the BIDS App that will process the dataset.")
    parser.add_argument("bids_dataset", help="BIDS dataset to be processed.")
    parser.add_argument("output_dir", help="Output directory.")
    parser.add_argument("--skip-group", action = 'store_true', help="Skips groups analysis.")
    parser.add_argument("--skip-subjects", metavar="FILE", type=lambda x: is_valid_file(parser, x), help="Skips subject labels in the text file.")
    args=parser.parse_args()
    boutiques_descriptor = os.path.join(os.path.abspath(args.bids_app_boutiques_descriptor))
    bids_dataset = args.bids_dataset
    output_dir = args.output_dir
    skipped_subjects = args.skip_subjects.read().split() if args.skip_subjects else []
    
    do_group_analysis = supports_group_analysis(boutiques_descriptor) and not args.skip_group
    do_group_string = "YES" if do_group_analysis else "NO"
    print("Computed Analyses: Subject [ YES ] - Group [ {0} ]".format(do_group_string))
    if len(skipped_subjects):
        print("Skipped subjects: {0}".format(skipped_subjects)) 
    
    # Spark initialization
    conf = SparkConf().setAppName("BIDS pipeline")
    sc = SparkContext(conf=conf)

    # RDD creation from BIDS datast
    rdd = create_subject_RDD(bids_dataset, sc) #create_RDD(bids_dataset,sc)
    

    # Uncomment to get a list of files by subject 
    # print(rdd.map(lambda x: list_files_by_subject(bids_dataset,x)).collect())

    # Participant analysis (done for all apps)
    mapped = rdd.filter(lambda x: get_subject_from_fn(x[0])  not in skipped_subjects)\
                .map(lambda x: run_subject_analysis(boutiques_descriptor, x[1], get_subject_from_fn(x[0]), output_dir))

    # Display results: careful: don't display results before all transformations have been applied to the RDD
    for result in mapped.collect():
        pretty_print(result)

    # Group analysis
    if do_group_analysis:
        group_result = run_group_analysis(boutiques_descriptor, bids_dataset, output_dir)
        pretty_print(group_result)
        
# Execute program
if  __name__ == "__main__":
    main()